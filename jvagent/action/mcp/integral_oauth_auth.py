"""``httpx`` auth flow that authenticates MCP requests to Integral Core with OAuth.

After the browser OAuth connect (see :mod:`jvagent.action.mcp_oauth.endpoints`),
Core issues an OAuth 2.1 access token scoped to the signed-in user.  The mounted
``/api/mcp`` endpoint accepts it as ``Authorization: Bearer <token>`` — the same
contract Core's resource-server auth middleware applies to any ``auth=True``
endpoint (``accept_oauth_bearer=True``).

``IntegralOAuthAuth`` loads the stored access token for a server/account, quietly
refreshes it when (near) expired, and stamps every request with the bearer.  An
optional ``X-Integral-Scope: ws:<id>`` header pins the workspace; Core honours it
only when the token's principal is a member, otherwise it fails closed to the
user's own default workspace.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator, Dict, Optional, Tuple

import httpx

from jvagent.action.mcp_oauth.integral_provider import (
    DEFAULT_ACCOUNT,
    INTEGRAL_SERVER,
    INTEGRAL_SERVICE,
    discover_authorization_server,
    load_registered_client,
    normalize_token_payload,
    payload_is_expired,
    refresh_access_token,
    resolve_scope,
)

logger = logging.getLogger(__name__)

DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_READ_TIMEOUT = 60.0


class IntegralOAuthNotConnectedError(RuntimeError):
    """Raised when no OAuth token is stored for the server/account."""


async def _load_token_node(
    server_name: str, account_name: str
) -> Tuple[Any, Optional[Dict[str, Any]]]:
    """Return ``(node, payload)`` for the stored token, or ``(None, None)``.

    Tolerates both storage shapes: a flat payload, and one nested under
    ``service_tokens`` (the shape ``apply_service_rebind`` writes).
    """
    import json

    from jvspatial.core.context import GraphContext
    from jvspatial.db import get_database_manager

    from jvagent.action.mcp_oauth.mcp_oauth_node import MCPOAuthToken

    manager = get_database_manager()
    db = manager.get_database()
    ctx = GraphContext(db)
    nodes = await ctx.find_nodes(
        MCPOAuthToken,
        {"context.server_name": server_name, "context.account_name": account_name},
    )
    if not nodes:
        return None, None
    node = nodes[0]
    if not node.token_json:
        return node, None
    try:
        payload = json.loads(node.token_json)
    except Exception:
        return node, None
    if not isinstance(payload, dict):
        return node, None
    return node, _flatten_service_tokens(payload)


def _flatten_service_tokens(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return a flat view of a token payload, unwrapping ``service_tokens``."""
    st = payload.get("service_tokens")
    if isinstance(st, dict):
        for key in (INTEGRAL_SERVICE, *st.keys()):
            blob = st.get(key)
            if isinstance(blob, dict) and (
                blob.get("access_token") or blob.get("refresh_token")
            ):
                merged = dict(payload)
                merged.update(blob)
                return merged
    return payload


async def _save_token_payload(node: Any, payload: Dict[str, Any]) -> None:
    import json

    node.token_json = json.dumps(payload)
    node.updated = datetime.now(timezone.utc)
    await node.save()


class IntegralOAuthAuth(httpx.Auth):
    """Bears the stored Integral OAuth access token, refreshing when stale."""

    requires_request_body = False
    requires_response_body = False

    def __init__(
        self,
        *,
        server_name: str = INTEGRAL_SERVER,
        account_name: str = DEFAULT_ACCOUNT,
        scope_id: str = "",
        scope: str = "",
        issuer: str = "",
        mcp_url: str = "",
    ) -> None:
        self._server_name = server_name
        self._account_name = account_name or DEFAULT_ACCOUNT
        self._scope_id = (scope_id or "").strip()
        self._scope = (scope or "").strip()
        self._issuer = (issuer or "").strip()
        self._mcp_url = (mcp_url or "").strip()
        self._lock = asyncio.Lock()
        self._cached_token = ""
        self._cached_expiry: Optional[datetime] = None

    def _cache_valid(self) -> bool:
        if not self._cached_token:
            return False
        if self._cached_expiry is None:
            return True
        return self._cached_expiry > datetime.now(timezone.utc) + timedelta(seconds=30)

    @staticmethod
    def _expiry_from_payload(payload: Dict[str, Any]) -> Optional[datetime]:
        raw = payload.get("expiry") or ""
        if not raw:
            return None
        try:
            expiry = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return expiry

    async def _valid_token(self) -> str:
        async with self._lock:
            if self._cache_valid():
                return self._cached_token
            node, payload = await _load_token_node(
                self._server_name, self._account_name
            )
            if payload and not payload_is_expired(payload):
                self._cached_token = str(
                    payload.get("access_token") or payload.get("token") or ""
                )
                self._cached_expiry = self._expiry_from_payload(payload)
                if self._cached_token:
                    return self._cached_token
            if not node or not payload:
                raise IntegralOAuthNotConnectedError(
                    f"No Integral OAuth token stored for "
                    f"{self._server_name}/{self._account_name}. "
                    "Connect via the agent's MCPs panel first."
                )
            return await self._refresh(node, payload)

    async def _refresh(self, node: Any, payload: Dict[str, Any]) -> str:
        refresh_token = str(payload.get("refresh_token") or "")
        client_id = str(payload.get("client_id") or "")
        if not refresh_token:
            raise IntegralOAuthNotConnectedError(
                "Stored Integral token has no refresh_token; reconnect via the "
                "agent's MCPs panel."
            )
        if not client_id:
            client = await load_registered_client(self._server_name)
            client_id = client.client_id if client else ""

        meta = await discover_authorization_server(
            mcp_url=self._mcp_url, issuer=self._issuer
        )
        scope = self._scope or resolve_scope()
        tokens = await refresh_access_token(
            meta,
            client_id=client_id,
            refresh_token=refresh_token,
            scope=scope,
        )
        new_payload = normalize_token_payload(
            tokens,
            client_id=client_id,
            token_uri=str(meta.get("token_endpoint") or payload.get("token_uri") or ""),
            account_name=self._account_name,
            scope=scope,
            previous=payload,
        )
        # Preserve display metadata from the original payload.
        for key in ("account_alias", "email", "mcp_services", "type"):
            if payload.get(key) is not None and new_payload.get(key) is None:
                new_payload[key] = payload[key]
        await _save_token_payload(node, new_payload)

        self._cached_token = str(new_payload.get("access_token") or "")
        self._cached_expiry = self._expiry_from_payload(new_payload)
        if not self._cached_token:
            raise IntegralOAuthNotConnectedError(
                "Integral token refresh returned no access token."
            )
        logger.info(
            "Refreshed Integral OAuth token for %s/%s",
            self._server_name,
            self._account_name,
        )
        return self._cached_token

    async def apply_auth(self, request: httpx.Request) -> None:
        """Set the bearer (and optional workspace scope) header on *request*."""
        token = await self._valid_token()
        request.headers["Authorization"] = f"Bearer {token}"
        if self._scope_id:
            request.headers["X-Integral-Scope"] = f"ws:{self._scope_id}"

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        await self.apply_auth(request)
        yield request


def _resolve(value: str, env_name: str) -> str:
    import os

    if value:
        return value.strip()
    if env_name:
        return (os.getenv(env_name) or "").strip()
    return ""


def build_integral_oauth_client(
    *,
    server_name: str = INTEGRAL_SERVER,
    account: str = "",
    account_env: str = "INTEGRAL_MCP_ACCOUNT",
    scope_id: str = "",
    scope_id_env: str = "INTEGRAL_MCP_WORKSPACE_ID",
    scope: str = "",
    issuer: str = "",
    issuer_env: str = "INTEGRAL_OAUTH_ISSUER",
    mcp_url: str = "",
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    read_timeout: float = DEFAULT_READ_TIMEOUT,
) -> httpx.AsyncClient:
    """Build an ``httpx.AsyncClient`` that bears the stored Integral OAuth token."""
    resolved_account = _resolve(account, account_env) or DEFAULT_ACCOUNT
    resolved_scope = _resolve(scope_id, scope_id_env)
    resolved_issuer = _resolve(issuer, issuer_env)
    timeout = httpx.Timeout(
        connect=connect_timeout, read=read_timeout, write=read_timeout, pool=None
    )
    auth = IntegralOAuthAuth(
        server_name=server_name,
        account_name=resolved_account,
        scope_id=resolved_scope,
        scope=scope,
        issuer=resolved_issuer,
        mcp_url=mcp_url,
    )

    async def _ensure_auth(request: httpx.Request) -> None:
        # httpx strips ``Authorization`` when it follows a *cross-origin* redirect
        # (Core's ``/api/mcp`` -> ``/api/mcp/`` on the API port) and does not
        # re-run a custom httpx.Auth flow for the redirect hop. Request event
        # hooks DO run for every hop, so re-apply the bearer when it is missing.
        if "authorization" in request.headers:
            return
        await auth.apply_auth(request)

    return httpx.AsyncClient(
        auth=auth,
        timeout=timeout,
        # The MCP SDK raises on any 3xx (``raise_for_status``), so follow
        # redirects; ``_ensure_auth`` re-applies the bearer on the redirect hop.
        follow_redirects=True,
        event_hooks={"request": [_ensure_auth]},
    )
