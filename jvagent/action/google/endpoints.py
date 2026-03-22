"""Centralized endpoints for Google OAuth."""

import html
import logging
import secrets
import string

from fastapi.responses import HTMLResponse
from jvspatial.api import endpoint
from jvspatial.api.exceptions import ResourceNotFoundError

from .google_action import GoogleAction

logger = logging.getLogger(__name__)

_OAUTH_THEMES = {
    "auth": {
        "primary": "#4285F4",
        "icon_bg": "rgba(66, 133, 244, 0.1)",
        "badge_bg": "rgba(66, 133, 244, 0.15)",
    },
    "success": {
        "primary": "#4CAF50",
        "icon_bg": "rgba(76, 175, 80, 0.1)",
        "badge_bg": "rgba(76, 175, 80, 0.15)",
    },
}


def _oauth_page_html(
    *,
    theme: str,
    title: str,
    icon_svg: str,
    body_inner: str,
) -> str:
    t = _OAUTH_THEMES[theme]
    primary = t["primary"]
    icon_bg = t["icon_bg"]
    badge_bg = t["badge_bg"]
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
                padding: 4px 12px;
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
                padding: 12px 32px;
                text-decoration: none;
                border-radius: 12px;
                font-weight: 600;
                font-size: 1rem;
                transition: transform 0.2s, box-shadow 0.2s;
                margin-top: 0.5rem;
            }}
            .auth-button:hover {{
                transform: translateY(-2px);
                box-shadow: 0 10px 15px -3px rgba(66, 133, 244, 0.4);
            }}
            .auth-button:active {{
                transform: translateY(0);
            }}
            .footer-info {{
                font-size: 0.8rem;
                color: var(--text-muted);
                margin-top: 2rem;
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
    body = f"<h1>Error</h1><p>{html.escape(message)}</p>"
    return HTMLResponse(content=body, status_code=status_code)


@endpoint(
    "/google/{action_id}",
    methods=["GET"],
    auth=False,
    tags=["Google OAuth"],
    summary="Get Google OAuth Authorization URL",
)
async def get_google_auth_url(action_id: str) -> HTMLResponse:
    """Get the Google OAuth2 authorization URL for any Google action.

    **Overview:**

    Generates a secure Google OAuth2 authorization URL for the specified action.
    This URL should be visited by the user to grant permissions to the application.
    The response is an HTML page that redirects the user or provides a button to start the flow.

    **Args:**

    - action_id: The unique identifier of the Google action to authorize (e.g., a Google Drive action ID).

    **Returns:**

    An HTMLResponse containing a stylish authorization page.

    **Raises:**

    - ResourceNotFoundError: If the specified action_id does not exist or is not a GoogleAction.
    """
    action = await GoogleAction.get(action_id)
    if not action or not isinstance(action, GoogleAction):
        raise ResourceNotFoundError(message=f"Google action {action_id} not found")

    alphabet = string.ascii_letters + string.digits + "-._~"
    code_verifier = "".join(secrets.choice(alphabet) for _ in range(64))

    auth_url = await action.get_authorization_url(code_verifier=code_verifier)
    logger.info("auth_url generated for action_id=%s", action_id)

    agent = await action.get_agent()
    agent_name = "Agent"
    agent_description = ""
    if agent:
        agent_name = getattr(agent, "alias", None) or getattr(agent, "name", "Agent")
        agent_description = getattr(agent, "description", "")

    action_label = html.escape(
        action.metadata.get("title", "Google Account").replace(" Action", "")
    )
    agent_name = html.escape(agent_name)
    agent_description = html.escape(agent_description) if agent_description else ""

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
            <h2>Grant Access</h2>
            <p style="color: var(--text-muted)">Authorize this application to access your {action_label}.</p>

            <div class="agent-info">
                <span class="agent-name">{agent_name}</span>
                {desc_html}
            </div>

            <a href="{auth_url}" class="auth-button">Authorize with Google</a>
    """
    html_content = _oauth_page_html(
        theme="auth",
        title="Google Authentication",
        icon_svg=icon_svg,
        body_inner=body_inner,
    )
    return HTMLResponse(content=html_content)


@endpoint(
    "/google/callback/",
    methods=["GET"],
    auth=False,
    tags=["Google OAuth"],
    summary="Google OAuth Callback Handler",
)
async def google_oauth_callback(code: str, state: str) -> HTMLResponse:
    """Handle the Google OAuth2 callback.

    **Overview:**

    This endpoint processes the authorization code returned by Google after a user grants access.
    It exchanges the code for access and refresh tokens, which are then securely stored.
    Standard state parameter is used to maintain security and identify the originating action.

    **Args:**

    - code: The authorization code provided by Google.
    - state: The state parameter containing action_id and PKCE verifier.

    **Returns:**

    An HTMLResponse indicating the success or failure of the authorization process.

    **Raises:**

    - None: Errors are handled and returned as HTML responses with appropriate status codes.
    """
    if not code or not state:
        return _oauth_error_html("Missing code or state.", status_code=400)

    parts = state.split(":", 1)
    action_id = parts[0]
    code_verifier = parts[1] if len(parts) > 1 else None

    logger.info("Processing Google OAuth callback for action: %s", action_id)

    action = await GoogleAction.get(action_id)
    if not action or not isinstance(action, GoogleAction):
        logger.error("Action %s not found during callback", action_id)
        return _oauth_error_html(f"Action {action_id} not found.", status_code=404)

    try:
        success = await action.authorize(code=code, code_verifier=code_verifier)
        if success:
            agent = await action.get_agent()
            agent_name = "Agent"
            agent_description = ""
            if agent:
                agent_name = getattr(agent, "alias", None) or getattr(
                    agent, "name", "Agent"
                )
                agent_description = getattr(agent, "description", "")

            action_label = html.escape(
                action.metadata.get("title", "Google Account").replace(" Action", "")
            )
            agent_name = html.escape(agent_name)
            agent_description = (
                html.escape(agent_description) if agent_description else ""
            )

            icon_svg = """
                <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" style="color: var(--primary)">
                    <polyline points="20 6 9 17 4 12"></polyline>
                </svg>
            """
            desc_html = (
                f'<p class="agent-desc">{agent_description}</p>'
                if agent_description
                else ""
            )
            body_inner = f"""
                    <div class="action-badge">Your {action_label} is connected successfully!</div>

                    <div class="agent-info">
                        <span class="agent-name">{agent_name}</span>
                        {desc_html}
                    </div>

                    <p class="close-text" style="color: var(--text-muted)">You can close this window.</p>
            """
            html_content = _oauth_page_html(
                theme="success",
                title="Authorization Successful",
                icon_svg=icon_svg,
                body_inner=body_inner,
            )
            logger.info("Authorization successful for %s", action_id)
            return HTMLResponse(content=html_content, status_code=200)

        return _oauth_error_html("Authorization failed.", status_code=400)
    except Exception:
        logger.error(
            "Callback error for action_id=%s",
            action_id,
            exc_info=True,
        )
        return _oauth_error_html(
            "Authorization could not be completed. Please try again or contact support.",
            status_code=400,
        )
