"""REST endpoints for no-code MCP OAuth client flow."""

import html
import json
import logging
import urllib.parse
from typing import Any, Dict, Optional

import httpx
from fastapi.responses import HTMLResponse
from jvspatial.api import endpoint
from jvspatial.api.exceptions import ResourceNotFoundError

from jvagent.action.oauth.state import consume_oauth_state, create_oauth_state
from jvagent.core.public_url import get_public_base_url

from .mcp_oauth_action import MCPOAuthAction

logger = logging.getLogger(__name__)

# Standard google-workspace-mcp scopes
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.settings.basic",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/forms.body",
    "https://www.googleapis.com/auth/forms.responses.readonly",
]


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
    "/mcp/{server_name}/auth",
    methods=["GET"],
    auth=False,
    tags=["MCP OAuth"],
    summary="Get Google OAuth Authorization URL for MCP Server",
)
async def get_mcp_auth_url(server_name: str, account: str = "integral") -> HTMLResponse:
    """Generate the Google OAuth2 authorization page for a given stdio MCP server."""
    action = await _get_mcp_oauth_action()
    if not action or not action.enabled:
        raise ResourceNotFoundError(message="MCPOAuthAction not enabled or found.")

    if server_name != "google_workspace":
        return _oauth_error_html(
            f"OAuth is not supported for server '{server_name}'. Only 'google_workspace' is supported.",
            400,
        )

    try:
        creds = _get_secrets()
    except Exception as exc:
        logger.error("Failed to load client secrets: %s", exc)
        return _oauth_error_html(str(exc), 400)

    redirect_uri = None
    # action.redirect_uri is a newline-separated "server_name: url" list
    raw_redirect = getattr(action, "redirect_uri", "") or ""
    for line in raw_redirect.splitlines():
        if line.strip().startswith(f"{server_name}:"):
            redirect_uri = line.split(":", 1)[1].strip()
            break
    if not redirect_uri:
        base_url = get_public_base_url()
        if not base_url:
            return _oauth_error_html(
                "JVAGENT_PUBLIC_BASE_URL is not set. A public base URL is required for OAuth callback.",
                400,
            )
        redirect_uri = f"{base_url.rstrip('/')}/api/mcp/{server_name}/auth/callback"

    # Create secure CSRF state token
    # We pass the account name inside the state mechanism
    # The state will be verified in the callback
    state_token = await create_oauth_state(
        action_id=f"mcp_oauth:{account}",
        provider="mcp_google",
        code_verifier="",  # Not using PKCE verifier for basic web flow
        redirect_uri=redirect_uri,
    )

    # Construct the Authorization URL
    params = {
        "client_id": creds["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state_token,
    }
    auth_url = (
        f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"
    )

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
        <h2>Grant Google Workspace Access</h2>
        <p style="color: var(--text-muted)">Authorize this application to access spreadsheets, drive, calendar and Gmail.</p>

        <div class="agent-info">
            <span class="agent-name">{agent_name}</span>
            {desc_html}
        </div>

        <a href="{auth_url}" class="auth-button">Authorize with Google</a>
    """
    html_content = _oauth_page_html(
        theme="auth",
        title="Google Workspace Authorization",
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
    """OAuth callback endpoint where Google redirects the browser."""
    if not code or not state:
        return _oauth_error_html("Missing code or state from OAuth provider.", 400)

    action = await _get_mcp_oauth_action()
    if not action or not action.enabled:
        return _oauth_error_html("MCPOAuthAction not found or disabled.", 400)

    # Consume the CSRF state token
    record = await consume_oauth_state(state, provider="mcp_google")
    if not record:
        logger.warning("MCP OAuth callback rejected: invalid or expired state")
        return _oauth_error_html("OAuth state is invalid or expired.", 400)

    # Extract account name from action_id ("mcp_oauth:{account_name}")
    action_parts = record.action_id.split(":")
    account_name = action_parts[1] if len(action_parts) > 1 else "integral"

    try:
        creds = _get_secrets()
    except Exception as exc:
        return _oauth_error_html(str(exc), 400)

    # Exchange authorization code for access and refresh tokens
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

    # Package token details matching what google-workspace-mcp expects in tokens/{account}.json
    payload = {
        "type": "authorized_user",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": tokens.get("refresh_token"),
    }

    if not payload["refresh_token"]:
        # If refresh_token is missing, we can't do offline operations. Explain clearly to user.
        return _oauth_error_html(
            "Did not receive a refresh token. Please go to your Google Account settings, "
            "remove the application permission, and authenticate again to grant offline access.",
            400,
        )

    # Store the credentials in the database node
    await action.save_oauth_token(
        server_name=server_name,
        account_name=account_name,
        token_data=payload,
    )

    # Trigger reboot/reload of the MCP client if active
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
            # Disconnect standard session so it spawns a fresh client with the new token next call
            await mcp._clear_session(server_name)
            logger.info("Cleared session for MCP server: %s", server_name)
    except Exception as exc:
        logger.warning("Failed to refresh MCP client session: %s", exc)

    # Success Page HTML
    icon_svg = """
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" style="color: var(--primary)">
            <polyline points="20 6 9 17 4 12"></polyline>
        </svg>
    """
    body_inner = f"""
        <div class="action-badge">Connection Successful!</div>
        <h2 style="color: var(--primary)">Google Workspace Connected</h2>
        <p style="color: var(--text-muted); line-height: 1.5;">Account <strong>{html.escape(account_name)}</strong> has been successfully authorized and persisted in the agent's secure store.</p>
        <p class="close-text" style="color: var(--text-muted)">You can close this window now.</p>
    """
    html_content = _oauth_page_html(
        theme="success",
        title="Authorization Successful",
        icon_svg=icon_svg,
        body_inner=body_inner,
    )
    return HTMLResponse(content=html_content)
