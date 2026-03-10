"""API endpoints for Google Drive action."""

import logging
from typing import Any, Dict, Optional

from jvspatial.api import endpoint
from jvspatial.api.exceptions import ResourceNotFoundError

from .google_drive_action import GoogleDriveAction

logger = logging.getLogger(__name__)

async def _get_drive_action(action_id: str):
    """Resolve action by ID; validate it is a GoogleDriveAction."""
    action = await GoogleDriveAction.get(action_id)
    if action and isinstance(action, GoogleDriveAction):
        return action
    return None

@endpoint(
    "/actions/{action_id}/google_drive/auth_url",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Google Drive Action"],
)
async def get_drive_auth_url(action_id: str) -> Dict[str, Any]:
    """Get the Google OAuth2 authorization URL."""
    action = await _get_drive_action(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Google Drive action {action_id} not found")

    auth_url = await action.get_authorization_url()
    return {"success": True, "auth_url": auth_url}

@endpoint(
    "/actions/{action_id}/google_drive/authorize",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Google Drive Action"],
)
async def authorize_drive(action_id: str, code: str) -> Dict[str, Any]:
    """Exchange the authorization code for credentials."""
    action = await _get_drive_action(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Google Drive action {action_id} not found")

    success = await action.authorize(code)
    return {"success": success}

@endpoint(
    "/actions/{action_id}/google_drive/upload",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Google Drive Action"],
)
async def upload_to_drive(
    action_id: str,
    name: str,
    content: Optional[str] = None,
    source_url: Optional[str] = None,
    mime_type: Optional[str] = None,
    parent_folder_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Upload a file to Google Drive."""
    action = await _get_drive_action(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Google Drive action {action_id} not found")

    result = await action.upload_file(
        name=name,
        content=content,
        source_url=source_url,
        mime_type=mime_type,
        parent_folder_id=parent_folder_id,
    )
    return {"success": True, "file": result}

@endpoint(
    "/actions/{action_id}/google_drive/list",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Google Drive Action"],
)
async def list_drive_files(action_id: str, folder_id: Optional[str] = None, page_size: int = 20) -> Dict[str, Any]:
    """List files in Google Drive."""
    action = await _get_drive_action(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Google Drive action {action_id} not found")

    files = await action.list_files(folder_id=folder_id, page_size=page_size)
    return {"success": True, "files": files}

@endpoint(
    "/actions/{action_id}/google_drive/share",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Google Drive Action"],
)
async def share_drive_file(
    action_id: str,
    file_id: str,
    share_type: str = "link",
    link_scope: str = "anyone",
    email: Optional[str] = None,
    role: str = "reader",
) -> Dict[str, Any]:
    """Share a file on Google Drive."""
    action = await _get_drive_action(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Google Drive action {action_id} not found")

    result = await action.share_file(
        file_id=file_id,
        share_type=share_type,
        link_scope=link_scope,
        email=email,
        role=role,
    )
    return {"success": True, "result": result}
