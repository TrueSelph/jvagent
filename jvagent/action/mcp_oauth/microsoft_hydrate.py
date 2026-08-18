"""Env overlay for @softeria/ms-365-mcp-server (BYOT ``MS365_MCP_OAUTH_TOKEN``).

Softeria does not refresh BYOT tokens. Refresh MCPOAuthToken first, then inject
the current access token into the stdio subprocess env.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from jvspatial.env import env

from .microsoft_scopes import MICROSOFT_365_SERVER

logger = logging.getLogger(__name__)


def microsoft_tenant_id() -> str:
    return (env("MICROSOFT_TENANT_ID") or "common").strip() or "common"


def microsoft_client_id() -> str:
    return str(env("MICROSOFT_CLIENT_ID") or "").strip()


def microsoft_client_secret() -> str:
    return str(env("MICROSOFT_CLIENT_SECRET") or "").strip()


def microsoft_token_url(tenant: Optional[str] = None) -> str:
    t = (tenant or microsoft_tenant_id()).strip() or "common"
    return f"https://login.microsoftonline.com/{t}/oauth2/v2.0/token"


def microsoft_authorize_url(tenant: Optional[str] = None) -> str:
    t = (tenant or microsoft_tenant_id()).strip() or "common"
    return f"https://login.microsoftonline.com/{t}/oauth2/v2.0/authorize"


def mcp_microsoft_365_auth_url(
    account: str = "integral",
    service: Optional[str] = None,
    base_url: Optional[str] = None,
) -> str:
    """Public browser URL for the MCP Microsoft OAuth start page."""
    from jvagent.core.public_url import get_public_base_url

    base = (base_url or get_public_base_url() or "").rstrip("/")
    params: List[str] = []
    if account:
        params.append(f"account={account}")
    if service:
        params.append(f"service={service}")
    q = f"?{'&'.join(params)}" if params else ""
    return f"{base}/api/mcp/{MICROSOFT_365_SERVER}/auth{q}"


def microsoft_365_stdio_env(token_data: Dict[str, Any]) -> Dict[str, str]:
    """Env overlay Softeria reads for BYOT Graph access."""
    overlay: Dict[str, str] = {}
    access = str(token_data.get("token") or "").strip()
    if access:
        overlay["MS365_MCP_OAUTH_TOKEN"] = access
    cid = microsoft_client_id() or str(token_data.get("client_id") or "").strip()
    if cid:
        overlay["MS365_MCP_CLIENT_ID"] = cid
    tenant = microsoft_tenant_id()
    if tenant:
        overlay["MS365_MCP_TENANT_ID"] = tenant
    secret = microsoft_client_secret()
    if secret:
        overlay["MS365_MCP_CLIENT_SECRET"] = secret
    return overlay


def merge_stdio_env(
    existing: Optional[Dict[str, str]], overlay: Dict[str, str]
) -> Dict[str, str]:
    """Overlay token env without dropping PATH when ``existing`` is None."""
    if existing is None:
        base = {str(k): str(v) for k, v in os.environ.items() if v is not None}
    else:
        base = {str(k): str(v) for k, v in existing.items()}
    base.update(overlay)
    return base


def _parse_expiry(raw: Any) -> Optional[datetime]:
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        exp = raw
    elif isinstance(raw, str):
        try:
            exp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return exp


def microsoft_access_token_expired(token_data: Dict[str, Any]) -> bool:
    token = str(token_data.get("token") or "").strip()
    if not token:
        return True
    exp = _parse_expiry(token_data.get("expiry"))
    if exp is None:
        return False
    return datetime.now(timezone.utc) >= exp


async def refresh_microsoft_365_token(
    token_data: Dict[str, Any],
    scopes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Refresh Graph tokens via Entra; return an updated payload dict."""
    refresh = str(token_data.get("refresh_token") or "").strip()
    if not refresh:
        raise ValueError("No Microsoft refresh_token to refresh")
    cid = microsoft_client_id() or str(token_data.get("client_id") or "").strip()
    if not cid:
        raise ValueError("Set MICROSOFT_CLIENT_ID to refresh Microsoft MCP tokens")
    secret = microsoft_client_secret()
    token_uri = str(token_data.get("token_uri") or "") or microsoft_token_url()
    data = {
        "client_id": cid,
        "grant_type": "refresh_token",
        "refresh_token": refresh,
    }
    if secret:
        data["client_secret"] = secret
    if scopes:
        data["scope"] = " ".join(scopes)
    async with httpx.AsyncClient() as client:
        resp = await client.post(token_uri, data=data, timeout=30.0)
        resp.raise_for_status()
        payload = resp.json()
    updated = dict(token_data)
    updated["type"] = "authorized_user"
    updated["token"] = payload.get("access_token") or ""
    updated["refresh_token"] = payload.get("refresh_token") or refresh
    updated["client_id"] = cid
    updated["token_uri"] = token_uri
    expires_in = int(payload.get("expires_in") or 3600)
    expiry = datetime.now(timezone.utc) + timedelta(seconds=max(0, expires_in - 60))
    updated["expiry"] = expiry.isoformat()
    scope_raw = payload.get("scope")
    if isinstance(scope_raw, str) and scope_raw.strip():
        updated["scopes"] = scope_raw.split()
    elif scopes:
        updated["scopes"] = list(scopes)
    return updated


async def load_microsoft_365_token_from_graph() -> (
    Tuple[Optional[str], Optional[Dict[str, Any]]]
):
    """Return ``(account_name, token_dict)`` for the first microsoft_365 row."""
    from jvspatial.core.context import GraphContext
    from jvspatial.db import get_database_manager

    from .mcp_oauth_node import MCPOAuthToken

    manager = get_database_manager()
    db = manager.get_database()
    ctx = GraphContext(db)
    nodes = await ctx.find_nodes(
        MCPOAuthToken, {"context.server_name": MICROSOFT_365_SERVER}
    )
    for node in nodes or []:
        raw = getattr(node, "token_json", "") or ""
        if not raw:
            continue
        try:
            token_data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(token_data, dict):
            continue
        account = str(getattr(node, "account_name", "") or "").strip()
        return account or None, token_data
    return None, None


async def apply_microsoft_365_stdio_env(
    existing: Optional[Dict[str, str]],
) -> Optional[Dict[str, str]]:
    """Refresh graph token if needed and return merged stdio env, or ``existing``."""
    from .mcp_oauth_action import hydrate_credential_payload

    account_name, token_data = await load_microsoft_365_token_from_graph()
    if not token_data:
        return existing
    blob = hydrate_credential_payload(token_data)
    if not blob.get("refresh_token"):
        return existing
    if microsoft_access_token_expired(blob):
        try:
            raw_scopes = blob.get("scopes")
            scopes: List[str] = []
            if isinstance(raw_scopes, str) and raw_scopes.strip():
                scopes = raw_scopes.split()
            elif isinstance(raw_scopes, list):
                scopes = [str(s).strip() for s in raw_scopes if str(s).strip()]
            blob = await refresh_microsoft_365_token(blob, scopes=scopes or None)
            from jvspatial.core.context import GraphContext
            from jvspatial.db import get_database_manager

            from .mcp_oauth_action import MCPOAuthAction

            manager = get_database_manager()
            db = manager.get_database()
            ctx = GraphContext(db)
            nodes = await ctx.find_nodes(MCPOAuthAction, {})
            oauth = nodes[0] if nodes else None
            if oauth is not None:
                last = str(token_data.get("last_authorized_service") or "").strip()
                await oauth.save_oauth_token_for_service(
                    MICROSOFT_365_SERVER,
                    account_name or "integral",
                    blob,
                    service=last or None,
                )
        except Exception as exc:
            logger.warning(
                "Microsoft MCP token refresh before stdio spawn failed: %s", exc
            )
            if not str(blob.get("token") or "").strip():
                return existing
    overlay = microsoft_365_stdio_env(blob)
    if not overlay.get("MS365_MCP_OAUTH_TOKEN"):
        return existing
    return merge_stdio_env(existing, overlay)
