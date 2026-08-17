import logging
from typing import Any, ClassVar, Dict, List, Optional, Tuple

import httpx
from jvspatial.core.annotations import attribute
from jvspatial.env import env

from jvagent.action.base import Action
from jvagent.action.oauth.audit import _audit_log_oauth_event

logger = logging.getLogger(__name__)

GRAPH_V1 = "https://graph.microsoft.com/v1.0"


class MicrosoftAction(Action):
    """Base class for Microsoft 365 Graph actions. Login is MCP OAuth (MCPOAuthToken)."""

    auth_url: str = attribute(
        default="",
        description="MCP Microsoft 365 login URL (set on startup).",
    )

    SCOPES: ClassVar[List[str]] = []

    _MCP_SERVER: ClassVar[str] = "microsoft_365"
    _MCP_ACCOUNT: ClassVar[str] = "integral"
    _MCP_SERVICE: ClassVar[str] = ""

    def _tenant_id(self) -> str:
        return (env("MICROSOFT_TENANT_ID") or "common").strip() or "common"

    def _client_id(self) -> str:
        return str(env("MICROSOFT_CLIENT_ID") or "").strip()

    def _client_secret(self) -> str:
        return str(env("MICROSOFT_CLIENT_SECRET") or "").strip()

    def _token_url(self) -> str:
        return (
            f"https://login.microsoftonline.com/{self._tenant_id()}/oauth2/v2.0/token"
        )

    async def _apply_env_defaults(self) -> None:
        """Point auth_url at MCP OAuth (`/api/mcp/microsoft_365/auth`)."""
        from jvagent.action.mcp_oauth.microsoft_hydrate import (
            mcp_microsoft_365_auth_url,
        )

        self.auth_url = mcp_microsoft_365_auth_url(
            self._MCP_ACCOUNT, service=self._MCP_SERVICE or None
        )
        await self.save()

    async def on_register(self) -> None:
        await self._apply_env_defaults()

    async def on_reload(self) -> None:
        await self._apply_env_defaults()

    async def on_startup(self) -> None:
        await self._apply_env_defaults()

    def _mcp_auth_url(self) -> str:
        from jvagent.action.mcp_oauth.microsoft_hydrate import (
            mcp_microsoft_365_auth_url,
        )

        return mcp_microsoft_365_auth_url(
            self._MCP_ACCOUNT, service=self._MCP_SERVICE or None
        )

    async def _mcp_oauth_action(self) -> Any:
        from jvagent.action.mcp_oauth.mcp_oauth_action import MCPOAuthAction

        return await self.get_action(MCPOAuthAction)

    async def _load_mcp_token(
        self,
    ) -> tuple[Optional[str], Optional[Dict[str, Any]], Any]:
        """Return ``(account_name, token_dict, node)`` for this action's MCP service."""
        from jvagent.action.mcp_oauth.mcp_oauth_action import (
            service_token_payload,
            token_row_for_service,
        )

        oauth = await self._mcp_oauth_action()
        if oauth is None:
            return None, None, None
        rows = await oauth.list_oauth_tokens(self._MCP_SERVER)
        if not rows:
            token = await oauth.get_oauth_token(self._MCP_SERVER, self._MCP_ACCOUNT)
            if token and self._MCP_SERVICE:
                token = service_token_payload(
                    token, self._MCP_SERVICE, self._MCP_SERVER
                )
            return (self._MCP_ACCOUNT, token, None) if token else (None, None, None)
        return token_row_for_service(
            rows, self._MCP_SERVER, self._MCP_SERVICE, self._MCP_ACCOUNT
        )

    def _scopes_for_refresh(self, token_data: Optional[Dict[str, Any]]) -> List[str]:
        """This action's granted Graph scopes only (no extra APIs)."""
        raw = (token_data or {}).get("scopes")
        granted: List[str] = []
        if isinstance(raw, str) and raw.strip():
            granted = raw.split()
        elif isinstance(raw, list):
            granted = [str(s).strip() for s in raw if str(s).strip()]
        if not granted:
            return []
        granted_set = set(granted)
        wanted = [s for s in (self.SCOPES or []) if s]
        if wanted:
            return [s for s in wanted if s in granted_set]
        return list(granted)

    async def _save_mcp_token(self, token_data: Dict[str, Any]) -> None:
        oauth = await self._mcp_oauth_action()
        if oauth is None:
            logger.warning(
                "MCPOAuthAction not found; cannot save refreshed %s credentials",
                self.__class__.__name__,
            )
            return
        account_name, existing, _node = await self._load_mcp_token()
        payload: Dict[str, Any] = dict(existing or {})
        payload.update(token_data)
        payload["type"] = "authorized_user"
        store_account = account_name or self._MCP_ACCOUNT
        await oauth.save_oauth_token_for_service(
            self._MCP_SERVER,
            store_account,
            payload,
            service=self._MCP_SERVICE or None,
        )
        _audit_log_oauth_event(
            provider="mcp_microsoft",
            event="token_saved",
            action_id=self.id,
            agent_id=self.agent_id,
            client_id_hint=payload.get("client_id"),
        )
        logger.info(
            "Saved MCP OAuth credentials for %s %s",
            self.__class__.__name__,
            self.id,
        )

    async def _get_access_token(self) -> str:
        """Load a Graph access token from MCPOAuthToken (not MicrosoftToken)."""
        from jvagent.action.mcp_oauth.microsoft_hydrate import (
            microsoft_access_token_expired,
            refresh_microsoft_365_token,
        )

        _account_name, token_data, _node = await self._load_mcp_token()
        label = self.__class__.__name__
        auth = self._mcp_auth_url()
        if not token_data or not token_data.get("refresh_token"):
            raise ValueError(
                f"No valid MCP OAuth credentials for {label}. Authorize at {auth}"
            )

        if microsoft_access_token_expired(token_data):
            refresh_scopes = self._scopes_for_refresh(token_data)
            if not refresh_scopes:
                raise ValueError(
                    f"No valid MCP OAuth credentials for {label}. Authorize at {auth}"
                )
            logger.info(
                "Refreshing expired MCP OAuth credentials for %s %s.",
                label,
                self.id,
            )
            try:
                token_data = await refresh_microsoft_365_token(
                    token_data, scopes=refresh_scopes
                )
                await self._save_mcp_token(token_data)
            except Exception as e:
                logger.error(
                    "Failed to refresh MCP OAuth credentials for %s: %s",
                    self.id,
                    e,
                )
                _audit_log_oauth_event(
                    provider="mcp_microsoft",
                    event="token_refresh_failed",
                    action_id=self.id,
                    agent_id=self.agent_id,
                    client_id_hint=token_data.get("client_id"),
                    extra_details={"error_type": type(e).__name__},
                )
                raise ValueError(
                    f"OAuth2 credentials for {label} expired and could not "
                    f"be refreshed. Re-authorize at {auth}"
                ) from e

        access = str(token_data.get("token") or "").strip()
        if not access:
            raise ValueError(
                f"No valid MCP OAuth credentials for {label}. Authorize at {auth}"
            )
        return access

    async def graph_request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: Optional[Dict[str, Any]] = None,
        content: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> httpx.Response:
        token = await self._get_access_token()
        url = (
            path
            if path.startswith("http")
            else f"{GRAPH_V1.rstrip('/')}/{path.lstrip('/')}"
        )
        h = {"Authorization": f"Bearer {token}"}
        if headers:
            h.update(headers)
        if json_body is not None and content is None:
            h.setdefault("Content-Type", "application/json")
        async with httpx.AsyncClient() as client:
            resp = await client.request(
                method,
                url,
                headers=h,
                params=params,
                json=json_body if content is None else None,
                content=content,
                timeout=120.0,
            )
            return resp

    async def graph_json(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: Optional[Dict[str, Any]] = None,
        ok: Tuple[int, ...] = (200, 201),
    ) -> Any:
        resp = await self.graph_request(
            method, path, json_body=json_body, params=params
        )
        if resp.status_code not in ok:
            detail = resp.text[:500]
            raise RuntimeError(f"Graph {method} {path} -> {resp.status_code}: {detail}")
        if resp.status_code == 204:
            return None
        if not resp.content:
            return None
        return resp.json()
