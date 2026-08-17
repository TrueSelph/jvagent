"""REST endpoints for no-code MCP OAuth client flow."""

import html
import json
import logging
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Set

import httpx
from fastapi.responses import HTMLResponse
from jvspatial.api import endpoint
from jvspatial.api.exceptions import ResourceNotFoundError

from jvagent.action.oauth.state import consume_oauth_state, create_oauth_state
from jvagent.core.public_url import get_public_base_url

from .mcp_oauth_action import (
    MCPOAuthAction,
    mcp_oauth_state_action_id,
    parse_mcp_oauth_state_action_id,
)
from .microsoft_hydrate import (
    microsoft_authorize_url,
    microsoft_client_id,
    microsoft_client_secret,
    microsoft_tenant_id,
    microsoft_token_url,
)
from .microsoft_scopes import (
    MICROSOFT_365_SERVER,
    MICROSOFT_ACTION_SERVICES,
)
from .microsoft_scopes import SERVICE_LABELS as MICROSOFT_SERVICE_LABELS
from .microsoft_scopes import (
    MicrosoftOAuthServiceNotEnabled,
    describe_microsoft_oauth_access,
    microsoft_365_tool_config,
    resolve_microsoft_oauth_scopes,
)
from .scopes import (
    GOOGLE_ACTION_SERVICES,
    GOOGLE_SERVICE_LABELS,
    GOOGLE_WORKSPACE_SERVER,
    GoogleOAuthServiceNotEnabled,
    describe_google_oauth_access,
    google_workspace_tool_config,
    granted_scopes_from_token_response,
    resolve_google_oauth_scopes,
)

logger = logging.getLogger(__name__)


async def _get_mcp_oauth_action() -> Optional[MCPOAuthAction]:
    try:
        from jvspatial.core.context import GraphContext
        from jvspatial.db import get_database_manager

        manager = get_database_manager()
        db = manager.get_database()
        ctx = GraphContext(db)
        nodes = await ctx.find_nodes(MCPOAuthAction, {})
        return nodes[0] if nodes else None
    except Exception as exc:
        logger.error("Failed to find MCPOAuthAction: %s", exc)
        return None


async def _google_workspace_mcp_tool_config() -> tuple:
    """``(tools_selector, denied_tools)`` from MCPAction yaml, or unrestricted."""
    try:
        from jvspatial.core.context import GraphContext
        from jvspatial.db import get_database_manager

        from jvagent.action.mcp.mcp_action import MCPAction

        manager = get_database_manager()
        db = manager.get_database()
        ctx = GraphContext(db)
        nodes = await ctx.find_nodes(MCPAction, {})
        mcp = nodes[0] if nodes else None
        servers = getattr(mcp, "servers", None) if mcp else None
        return google_workspace_tool_config(servers)
    except Exception as exc:
        logger.warning("Failed to read MCPAction Google Workspace tools: %s", exc)
        return "-all", []


async def _enabled_oauth_services(
    oauth_action: Optional[MCPOAuthAction],
    action_services: tuple,
    *,
    label: str,
) -> Set[str]:
    """Services whose sibling *Action is enabled on the same agent."""
    enabled: Set[str] = set()
    if oauth_action is None:
        return enabled
    try:
        agent = await oauth_action.get_agent()
        if not agent:
            return enabled
        for type_name, service in action_services:
            sibling = await agent.get_action_by_type(type_name)
            if sibling and getattr(sibling, "enabled", True):
                enabled.add(service)
    except Exception as exc:
        logger.warning(
            "Failed to list enabled %s actions for MCP OAuth: %s", label, exc
        )
    return enabled


async def _enabled_google_oauth_services(
    oauth_action: Optional[MCPOAuthAction],
) -> Set[str]:
    return await _enabled_oauth_services(
        oauth_action, GOOGLE_ACTION_SERVICES, label="Google"
    )


async def _microsoft_365_mcp_tool_config() -> tuple:
    """``(tools_selector, denied_tools)`` from MCPAction yaml, or none."""
    try:
        from jvspatial.core.context import GraphContext
        from jvspatial.db import get_database_manager

        from jvagent.action.mcp.mcp_action import MCPAction

        manager = get_database_manager()
        db = manager.get_database()
        ctx = GraphContext(db)
        nodes = await ctx.find_nodes(MCPAction, {})
        mcp = nodes[0] if nodes else None
        servers = getattr(mcp, "servers", None) if mcp else None
        return microsoft_365_tool_config(servers)
    except Exception as exc:
        logger.warning("Failed to read MCPAction Microsoft 365 tools: %s", exc)
        return "-all", []


async def _enabled_microsoft_oauth_services(
    oauth_action: Optional[MCPOAuthAction],
) -> Set[str]:
    return await _enabled_oauth_services(
        oauth_action, MICROSOFT_ACTION_SERVICES, label="Microsoft"
    )


def _microsoft_client() -> Dict[str, str]:
    cid = microsoft_client_id()
    if not cid:
        raise ValueError("MICROSOFT_CLIENT_ID is not configured in the environment.")
    return {
        "client_id": cid,
        "client_secret": microsoft_client_secret(),
        "tenant_id": microsoft_tenant_id(),
    }


def _redirect_uri_for_server(action: MCPOAuthAction, server_name: str) -> Optional[str]:
    for item in getattr(action, "oauth_setup", None) or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("server") or "") != server_name:
            continue
        uri = str(item.get("redirect_uri") or "").strip()
        if uri:
            return uri
    raw_redirect = getattr(action, "redirect_uri", "") or ""
    if isinstance(raw_redirect, str):
        for line in raw_redirect.splitlines():
            if line.strip().startswith(f"{server_name}:"):
                return line.split(":", 1)[1].strip()
    base_url = get_public_base_url()
    if not base_url:
        return None
    return f"{base_url.rstrip('/')}/api/mcp/{server_name}/auth/callback"


async def _clear_mcp_session(server_name: str) -> None:
    try:
        from jvspatial.core.context import GraphContext
        from jvspatial.db import get_database_manager

        from jvagent.action.mcp.mcp_action import MCPAction

        manager = get_database_manager()
        db = manager.get_database()
        ctx = GraphContext(db)
        nodes = await ctx.find_nodes(MCPAction, {})
        mcp = nodes[0] if nodes else None
        if mcp:
            await mcp._clear_session(server_name)
            logger.info("Cleared session for MCP server: %s", server_name)
    except Exception as exc:
        logger.warning("Failed to refresh MCP client session: %s", exc)


def _get_secrets() -> Dict[str, Any]:
    """Parse GOOGLE_CLIENT_SECRETS_JSON from environment."""
    import os

    raw = os.environ.get("GOOGLE_CLIENT_SECRETS_JSON", "").strip()
    if not raw:
        raise ValueError(
            "GOOGLE_CLIENT_SECRETS_JSON is not configured in the environment."
        )
    data = json.loads(raw)
    web_or_installed = data.get("web") or data.get("installed")
    if not web_or_installed:
        raise ValueError(
            "Invalid client secrets format: expected 'web' or 'installed' root key."
        )
    return web_or_installed


def _oauth_page_html(
    *,
    theme: str,
    title: str,
    icon_svg: str,
    body_inner: str,
) -> str:
    primary = "#4285F4" if theme == "auth" else "#4CAF50"
    icon_bg = "rgba(66, 133, 244, 0.1)" if theme == "auth" else "rgba(76, 175, 80, 0.1)"
    badge_bg = (
        "rgba(66, 133, 244, 0.15)" if theme == "auth" else "rgba(76, 175, 80, 0.15)"
    )
    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{title}</title>
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600&display=swap" rel="stylesheet">
        <style>
            :root {{
                --primary: {primary};
                --bg: #0f172a;
                --card-bg: rgba(30, 41, 59, 0.7);
                --text: #f8fafc;
                --text-muted: #94a3b8;
            }}
            body {{
                font-family: 'Outfit', sans-serif;
                background-color: var(--bg);
                color: var(--text);
                display: flex;
                align-items: center;
                justify-content: center;
                min-height: 100vh;
                margin: 0;
                overflow: hidden;
            }}
            .container {{
                background: var(--card-bg);
                backdrop-filter: blur(12px);
                -webkit-backdrop-filter: blur(12px);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 24px;
                padding: 3rem;
                max-width: 450px;
                width: 90%;
                text-align: center;
                box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
                animation: slideUp 0.6s cubic-bezier(0.16, 1, 0.3, 1);
            }}
            @keyframes slideUp {{
                from {{ opacity: 0; transform: translateY(20px); }}
                to {{ opacity: 1; transform: translateY(0); }}
            }}
            .icon-circle {{
                width: 80px;
                height: 80px;
                background: {icon_bg};
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                margin: 0 auto 1.5rem;
                border: 2px solid var(--primary);
            }}
            h2 {{
                color: var(--primary);
                font-weight: 600;
                margin-top: 0;
                font-size: 1.75rem;
            }}
            .action-badge {{
                display: inline-block;
                padding: 6px 14px;
                background: {badge_bg};
                color: var(--primary);
                border-radius: 8px;
                font-size: 0.85rem;
                font-weight: 600;
                letter-spacing: 0.05em;
                text-transform: uppercase;
                margin-bottom: 1rem;
            }}
            .agent-info {{
                margin: 1.5rem 0 2rem;
                padding: 1.5rem;
                background: rgba(255, 255, 255, 0.03);
                border-radius: 16px;
                border: 1px solid rgba(255, 255, 255, 0.05);
            }}
            .agent-name {{
                font-size: 1.25rem;
                font-weight: 600;
                display: block;
                margin-bottom: 0.5rem;
            }}
            .agent-desc {{
                font-size: 0.95rem;
                color: var(--text-muted);
                line-height: 1.5;
            }}
            .auth-button {{
                display: inline-block;
                background-color: var(--primary);
                color: white;
                padding: 14px 36px;
                text-decoration: none;
                border-radius: 12px;
                font-weight: 600;
                font-size: 1rem;
                transition: transform 0.2s, box-shadow 0.2s;
                margin-top: 0.5rem;
                border: none;
                cursor: pointer;
            }}
            .auth-button:hover {{
                transform: translateY(-2px);
                box-shadow: 0 10px 15px -3px rgba(66, 133, 244, 0.4);
            }}
            .auth-button:active {{
                transform: translateY(0);
            }}
            .close-text {{
                margin-top: 1rem;
                font-size: 0.9rem;
                opacity: 0.8;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="icon-circle">
                {icon_svg}
            </div>
            {body_inner}
        </div>
    </body>
    </html>
    """


def _oauth_error_html(message: str, status_code: int = 400) -> HTMLResponse:
    body = _oauth_page_html(
        theme="auth",
        title="Authorization Error",
        icon_svg="""
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#FF5252" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="12" cy="12" r="10"></circle>
                <line x1="12" y1="8" x2="12" y2="12"></line>
                <line x1="12" y1="16" x2="12.01" y2="16"></line>
            </svg>
        """,
        body_inner=f"""
            <h2 style="color: #FF5252">Authentication Failed</h2>
            <p style="color: var(--text-muted); line-height: 1.5;">{html.escape(message)}</p>
            <p class="close-text" style="color: var(--text-muted)">Please check configuration and try again.</p>
        """,
    )
    return HTMLResponse(content=body, status_code=status_code)


@endpoint(
    "/mcp/{server_name}/auth/status",
    methods=["GET"],
    auth=True,
    tags=["MCP OAuth"],
    summary="MCP OAuth connection status for a server",
)
async def get_mcp_auth_status(server_name: str) -> Dict[str, Any]:
    """Return per-service connected emails for an MCP OAuth server."""
    supported = server_name in (GOOGLE_WORKSPACE_SERVER, MICROSOFT_365_SERVER)
    bindings: Dict[str, Dict[str, str]] = {}
    if supported:
        action = await _get_mcp_oauth_action()
        if action:
            bindings = await action.oauth_bindings_for_server(server_name)
    return {
        "oauth_supported": supported,
        "server_name": server_name,
        "bindings": bindings,
    }


@endpoint(
    "/mcp/{server_name}/auth",
    methods=["GET"],
    auth=False,
    tags=["MCP OAuth"],
    summary="Get OAuth Authorization URL for MCP Server",
)
async def get_mcp_auth_url(
    server_name: str, account: str = "integral", service: str = ""
) -> HTMLResponse:
    """Generate the OAuth2 authorization page for a stdio MCP server."""
    action = await _get_mcp_oauth_action()
    if not action or not action.enabled:
        raise ResourceNotFoundError(message="MCPOAuthAction not enabled or found.")

    if server_name not in (GOOGLE_WORKSPACE_SERVER, MICROSOFT_365_SERVER):
        return _oauth_error_html(
            f"OAuth is not supported for server '{server_name}'. "
            f"Supported: '{GOOGLE_WORKSPACE_SERVER}', '{MICROSOFT_365_SERVER}'.",
            400,
        )

    redirect_uri = _redirect_uri_for_server(action, server_name)
    if not redirect_uri:
        return _oauth_error_html(
            "JVAGENT_PUBLIC_BASE_URL is not set. A public base URL is required for OAuth callback.",
            400,
        )

    if server_name == MICROSOFT_365_SERVER:
        return await _microsoft_auth_page(
            action, account=account, service=service, redirect_uri=redirect_uri
        )
    return await _google_auth_page(
        action, account=account, service=service, redirect_uri=redirect_uri
    )


async def _google_auth_page(
    action: MCPOAuthAction,
    *,
    account: str,
    service: str,
    redirect_uri: str,
) -> HTMLResponse:
    try:
        creds = _get_secrets()
    except Exception as exc:
        logger.error("Failed to load client secrets: %s", exc)
        return _oauth_error_html(str(exc), 400)

    tools_selector, denied_tools = await _google_workspace_mcp_tool_config()
    enabled_services = await _enabled_google_oauth_services(action)
    try:
        scopes = resolve_google_oauth_scopes(
            tools_selector,
            denied_tools,
            service=service or None,
            enabled_services=enabled_services,
        )
    except GoogleOAuthServiceNotEnabled as exc:
        return _oauth_error_html(str(exc), 400)

    state_token = await create_oauth_state(
        action_id=mcp_oauth_state_action_id(account, service),
        provider="mcp_google",
        code_verifier="",
        redirect_uri=redirect_uri,
    )
    params = {
        "client_id": creds["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent",
        "state": state_token,
    }
    auth_url = (
        f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"
    )
    return await _grant_html(
        action,
        title="Google Workspace Authorization",
        heading="Grant Google Workspace Access",
        access_copy=describe_google_oauth_access(scopes),
        button_label="Authorize with Google",
        auth_url=auth_url,
    )


async def _microsoft_auth_page(
    action: MCPOAuthAction,
    *,
    account: str,
    service: str,
    redirect_uri: str,
) -> HTMLResponse:
    try:
        creds = _microsoft_client()
    except Exception as exc:
        logger.error("Failed to load Microsoft client config: %s", exc)
        return _oauth_error_html(str(exc), 400)

    tools_selector, denied_tools = await _microsoft_365_mcp_tool_config()
    enabled_services = await _enabled_microsoft_oauth_services(action)
    try:
        scopes = resolve_microsoft_oauth_scopes(
            tools_selector,
            denied_tools,
            service=service or None,
            enabled_services=enabled_services,
        )
    except MicrosoftOAuthServiceNotEnabled as exc:
        return _oauth_error_html(str(exc), 400)

    state_token = await create_oauth_state(
        action_id=mcp_oauth_state_action_id(account, service),
        provider="mcp_microsoft",
        code_verifier="",
        redirect_uri=redirect_uri,
    )
    params = {
        "client_id": creds["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "response_mode": "query",
        "scope": " ".join(scopes),
        "prompt": "consent",
        "state": state_token,
    }
    auth_url = (
        microsoft_authorize_url(creds["tenant_id"])
        + "?"
        + urllib.parse.urlencode(params)
    )
    return await _grant_html(
        action,
        title="Microsoft 365 Authorization",
        heading="Grant Microsoft 365 Access",
        access_copy=describe_microsoft_oauth_access(scopes),
        button_label="Authorize with Microsoft",
        auth_url=auth_url,
    )


async def _grant_html(
    action: MCPOAuthAction,
    *,
    title: str,
    heading: str,
    access_copy: str,
    button_label: str,
    auth_url: str,
) -> HTMLResponse:
    agent = await action.get_agent()
    agent_name = html.escape(agent.alias or agent.name or "Agent") if agent else "Agent"
    agent_description = html.escape(agent.description or "") if agent else ""
    icon_svg = """
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="color: var(--primary)">
            <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
            <path d="M7 11V7a5 5 0 0 1 10 0v4"></path>
        </svg>
    """
    desc_html = (
        f'<p class="agent-desc">{agent_description}</p>' if agent_description else ""
    )
    body_inner = f"""
        <h2>{html.escape(heading)}</h2>
        <p style="color: var(--text-muted)">{html.escape(access_copy)}</p>

        <div class="agent-info">
            <span class="agent-name">{agent_name}</span>
            {desc_html}
        </div>

        <a href="{html.escape(auth_url)}" class="auth-button">{html.escape(button_label)}</a>
    """
    html_content = _oauth_page_html(
        theme="auth",
        title=title,
        icon_svg=icon_svg,
        body_inner=body_inner,
    )
    return HTMLResponse(content=html_content)


@endpoint(
    "/mcp/{server_name}/auth/callback",
    methods=["GET"],
    auth=False,
    tags=["MCP OAuth"],
    summary="Handle OAuth callback for stdio MCP server",
)
async def mcp_oauth_callback(server_name: str, code: str, state: str) -> HTMLResponse:
    """OAuth callback where Google or Microsoft redirects the browser."""
    if not code or not state:
        return _oauth_error_html("Missing code or state from OAuth provider.", 400)

    action = await _get_mcp_oauth_action()
    if not action or not action.enabled:
        return _oauth_error_html("MCPOAuthAction not found or disabled.", 400)

    if server_name == GOOGLE_WORKSPACE_SERVER:
        provider = "mcp_google"
    elif server_name == MICROSOFT_365_SERVER:
        provider = "mcp_microsoft"
    else:
        return _oauth_error_html(
            f"OAuth is not supported for server '{server_name}'.",
            400,
        )

    record = await consume_oauth_state(state, provider=provider)
    if not record:
        logger.warning("MCP OAuth callback rejected: invalid or expired state")
        return _oauth_error_html("OAuth state is invalid or expired.", 400)

    account_name, service = parse_mcp_oauth_state_action_id(record.action_id)

    if server_name == MICROSOFT_365_SERVER:
        result = await _exchange_microsoft_code(
            action, code, record, account_name, service=service
        )
    else:
        result = await _exchange_google_code(
            action, code, record, account_name, service=service
        )
    if isinstance(result, HTMLResponse):
        return result
    store_account, connected_label = result

    await _clear_mcp_session(server_name)

    icon_svg = """
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" style="color: var(--primary)">
            <polyline points="20 6 9 17 4 12"></polyline>
        </svg>
    """
    body_inner = f"""
        <div class="action-badge">Connection Successful!</div>
        <h2 style="color: var(--primary)">{html.escape(connected_label)} Connected</h2>
        <p style="color: var(--text-muted); line-height: 1.5;">Account <strong>{html.escape(store_account)}</strong> has been successfully authorized and persisted in the agent's secure store.</p>
        <p class="close-text" style="color: var(--text-muted)">You can close this window now.</p>
    """
    html_content = _oauth_page_html(
        theme="success",
        title="Authorization Successful",
        icon_svg=icon_svg,
        body_inner=body_inner,
    )
    return HTMLResponse(content=html_content)


async def _exchange_google_code(
    action: MCPOAuthAction,
    code: str,
    record: Any,
    account_name: str,
    service: str = "",
) -> HTMLResponse | tuple[str, str]:
    try:
        creds = _get_secrets()
    except Exception as exc:
        return _oauth_error_html(str(exc), 400)

    logger.info("Exchanging auth code for tokens for account: %s", account_name)
    token_url = "https://oauth2.googleapis.com/token"
    data = {
        "code": code,
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "redirect_uri": record.redirect_uri,
        "grant_type": "authorization_code",
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(token_url, data=data)
        if response.status_code != 200:
            logger.error("Token exchange failed: %s", response.text)
            return _oauth_error_html(f"Token exchange failed: {response.text}", 400)
        tokens = response.json()

    tools_selector, denied_tools = await _google_workspace_mcp_tool_config()
    enabled_services = await _enabled_google_oauth_services(action)
    fallback_scopes = resolve_google_oauth_scopes(
        tools_selector,
        denied_tools,
        service=service or None,
        enabled_services=enabled_services,
    )
    scopes = granted_scopes_from_token_response(tokens, fallback_scopes)

    payload = {
        "type": "authorized_user",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": tokens.get("refresh_token"),
        "token": tokens.get("access_token") or "",
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": scopes,
        "account_alias": account_name,
    }
    if service:
        payload["mcp_services"] = [service]

    if not payload["refresh_token"]:
        return _oauth_error_html(
            "Did not receive a refresh token. Please go to your Google Account settings, "
            "remove the application permission, and authenticate again to grant offline access.",
            400,
        )

    access_token = tokens.get("access_token") or ""
    email = ""
    if access_token:
        try:
            async with httpx.AsyncClient() as client:
                info_resp = await client.get(
                    "https://www.googleapis.com/oauth2/v2/userinfo",
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=15.0,
                )
            if info_resp.status_code == 200:
                email = str((info_resp.json() or {}).get("email") or "").strip()
        except Exception as exc:
            logger.warning("Failed to fetch Google userinfo email: %s", exc)
    if email:
        payload["email"] = email
        store_account = email
    else:
        store_account = account_name

    await action.save_oauth_token_for_service(
        GOOGLE_WORKSPACE_SERVER,
        store_account,
        payload,
        service=service or None,
    )
    label = (
        GOOGLE_SERVICE_LABELS.get(service, "Google Workspace")
        if service
        else "Google Workspace"
    )
    return store_account, label


async def _exchange_microsoft_code(
    action: MCPOAuthAction,
    code: str,
    record: Any,
    account_name: str,
    service: str = "",
) -> HTMLResponse | tuple[str, str]:
    try:
        creds = _microsoft_client()
    except Exception as exc:
        return _oauth_error_html(str(exc), 400)

    logger.info(
        "Exchanging Microsoft auth code for tokens for account: %s", account_name
    )
    token_url = microsoft_token_url(creds["tenant_id"])
    data = {
        "code": code,
        "client_id": creds["client_id"],
        "redirect_uri": record.redirect_uri,
        "grant_type": "authorization_code",
    }
    if creds["client_secret"]:
        data["client_secret"] = creds["client_secret"]

    async with httpx.AsyncClient() as client:
        response = await client.post(token_url, data=data)
        if response.status_code != 200:
            logger.error("Microsoft token exchange failed: %s", response.text)
            return _oauth_error_html(f"Token exchange failed: {response.text}", 400)
        tokens = response.json()

    tools_selector, denied_tools = await _microsoft_365_mcp_tool_config()
    enabled_services = await _enabled_microsoft_oauth_services(action)
    fallback_scopes = resolve_microsoft_oauth_scopes(
        tools_selector,
        denied_tools,
        service=service or None,
        enabled_services=enabled_services,
    )
    scopes = granted_scopes_from_token_response(tokens, fallback_scopes)

    expires_in = int(tokens.get("expires_in") or 3600)
    expiry = datetime.now(timezone.utc) + timedelta(seconds=max(0, expires_in - 60))
    payload: Dict[str, Any] = {
        "type": "authorized_user",
        "client_id": creds["client_id"],
        "refresh_token": tokens.get("refresh_token"),
        "token": tokens.get("access_token") or "",
        "token_uri": token_url,
        "scopes": scopes,
        "account_alias": account_name,
        "expiry": expiry.isoformat(),
    }
    if service:
        payload["mcp_services"] = [service]

    if not payload["refresh_token"]:
        return _oauth_error_html(
            "Did not receive a refresh token. Ensure the Entra app requests "
            "offline_access, then remove this app in your Microsoft account "
            "permissions and authorize again.",
            400,
        )

    access_token = tokens.get("access_token") or ""
    email = ""
    if access_token:
        try:
            async with httpx.AsyncClient() as client:
                info_resp = await client.get(
                    "https://graph.microsoft.com/v1.0/me",
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=15.0,
                )
            if info_resp.status_code == 200:
                me = info_resp.json() or {}
                email = str(me.get("mail") or me.get("userPrincipalName") or "").strip()
        except Exception as exc:
            logger.warning("Failed to fetch Microsoft Graph /me email: %s", exc)
    if email:
        payload["email"] = email
        store_account = email
    else:
        store_account = account_name

    await action.save_oauth_token_for_service(
        MICROSOFT_365_SERVER,
        store_account,
        payload,
        service=service or None,
    )
    label = (
        MICROSOFT_SERVICE_LABELS.get(service, "Microsoft 365")
        if service
        else "Microsoft 365"
    )
    return store_account, label
