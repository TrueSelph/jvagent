"""API endpoints for Google Gmail action."""

import logging
from typing import Any, Dict, Optional

from jvspatial.api import endpoint
from jvspatial.api.exceptions import ResourceNotFoundError

from .google_gmail_action import GoogleGmailAction

logger = logging.getLogger(__name__)

async def _get_gmail_action(action_id: str):
    """Resolve action by ID; validate it is a GoogleGmailAction."""
    action = await GoogleGmailAction.get(action_id)
    if action and isinstance(action, GoogleGmailAction):
        return action
    return None

@endpoint(
    "/actions/{action_id}/google_gmail/auth_url",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Google Gmail Action"],
)
async def get_gmail_auth_url(action_id: str) -> Dict[str, Any]:
    """Get the Google OAuth2 authorization URL."""
    action = await _get_gmail_action(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Google Gmail action {action_id} not found")

    auth_url = await action.get_authorization_url()
    return {"success": True, "auth_url": auth_url}

@endpoint(
    "/actions/{action_id}/google_gmail/authorize",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Google Gmail Action"],
)
async def authorize_gmail(action_id: str, code: str) -> Dict[str, Any]:
    """Exchange the authorization code for credentials."""
    action = await _get_gmail_action(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Google Gmail action {action_id} not found")

    success = await action.authorize(code)
    return {"success": success}

@endpoint(
    "/actions/{action_id}/google_gmail/send",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Google Gmail Action"],
)
async def send_gmail(
    action_id: str,
    to: str,
    subject: str,
    body: str,
    user_id: str = "me",
) -> Dict[str, Any]:
    """Send an email via Gmail."""
    action = await _get_gmail_action(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Google Gmail action {action_id} not found")

    result = await action.send_email(
        to=to,
        subject=subject,
        body=body,
        user_id=user_id,
    )
    return {"success": True, "result": result}

@endpoint(
    "/actions/{action_id}/google_gmail/list",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Google Gmail Action"],
)
async def list_gmail_messages(action_id: str, query: str = '', max_results: int = 10) -> Dict[str, Any]:
    """List Gmail messages."""
    action = await _get_gmail_action(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Google Gmail action {action_id} not found")

    messages = await action.list_messages(query=query, max_results=max_results)
    return {"success": True, "messages": messages}

@endpoint(
    "/actions/{action_id}/google_gmail/profile",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Google Gmail Action"],
)
async def get_gmail_profile(action_id: str, user_id: str = 'me') -> Dict[str, Any]:
    """Get Gmail profile info."""
    action = await _get_gmail_action(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Google Gmail action {action_id} not found")

    profile = await action.get_profile(user_id=user_id)
    return {"success": True, "profile": profile}
