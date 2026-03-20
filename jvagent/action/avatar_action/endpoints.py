"""API endpoints for Avatar action.

This module defines HTTP endpoints for setting, getting and deleting agent avatars.
"""

import logging
from typing import Any, Dict, Optional

from jvspatial.api import endpoint
from jvspatial.api.exceptions import ResourceNotFoundError

from .avatar_action import AvatarAction

logger = logging.getLogger(__name__)


async def _get_avatar_action(action_id: str) -> Optional[AvatarAction]:
    """Resolve action by ID; validate it is an AvatarAction."""
    action = await AvatarAction.get(action_id)
    if action and isinstance(action, AvatarAction):
        return action
    return None


@endpoint(
    "/actions/{action_id}/avatar",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Avatar Action"],
)
async def set_avatar(action_id: str, data: Any) -> Dict[str, Any]:
    """Set the agent's avatar image.

    Args:
        action_id: ID of the Avatar action instance
        data: Request data. Can be a dictionary with 'image_data' and 'mimetype',
              or a data URI string (e.g. 'data:image/png;base64,...').

    Returns:
        Status result
    """
    action = await _get_avatar_action(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Avatar action with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    image_data = None
    mimetype = None

    if isinstance(data, str):
        # Handle raw string (possible data URI)
        if data.startswith("data:"):
            try:
                header, base64_data = data.split(",", 1)
                mimetype = header.split(";")[0].split(":")[1]
                image_data = base64_data
            except Exception as e:
                logger.error(f"Failed to parse data URI string: {e}")
    elif isinstance(data, dict):
        image_data = data.get("image_data")
        mimetype = data.get("mimetype")

        # If not provided explicitly, check if the first value is a data URI
        if not image_data and len(data) > 0:
            # Check all values for a data URI
            for val in data.values():
                if isinstance(val, str) and val.startswith("data:"):
                    try:
                        header, base64_data = val.split(",", 1)
                        mimetype = header.split(";")[0].split(":")[1]
                        image_data = base64_data
                        break
                    except Exception:
                        continue

    if not image_data or not mimetype:
        return {
            "success": False,
            "error": "Could not extract image_data and mimetype. Provide them explicitly or use a data URI.",
        }

    success = await action.set_avatar(image_data, mimetype)

    return {
        "success": success,
        "message": "Avatar set successfully" if success else "Failed to set avatar",
    }


@endpoint(
    "/actions/{action_id}/avatar",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Avatar Action"],
)
async def get_avatar(action_id: str) -> Dict[str, Any]:
    """Get the agent's avatar image.

    Args:
        action_id: ID of the Avatar action instance

    Returns:
        Avatar data URI
    """
    action = await _get_avatar_action(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Avatar action with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    avatar_uri = action.get_avatar(with_prefix=True)

    return {
        "success": avatar_uri is not None,
        "avatar": avatar_uri,
        "set": avatar_uri is not None,
    }


@endpoint(
    "/actions/{action_id}/avatar",
    methods=["DELETE"],
    auth=True,
    roles=["admin"],
    tags=["Avatar Action"],
)
async def delete_avatar(action_id: str) -> Dict[str, Any]:
    """Delete the agent's avatar image.

    Args:
        action_id: ID of the Avatar action instance

    Returns:
        Status result
    """
    action = await _get_avatar_action(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Avatar action with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    success = await action.delete_avatar()

    return {
        "success": success,
        "message": "Avatar deleted successfully" if success else "Failed to delete avatar",
    }


@endpoint(
    "/actions/{action_id}/avatar/health",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Avatar Action"],
)
async def avatar_health_check(action_id: str) -> Dict[str, Any]:
    """Check Avatar action health.

    Args:
        action_id: ID of the Avatar action instance

    Returns:
        Health check result
    """
    action = await _get_avatar_action(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Avatar action with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    health = await action.healthcheck()

    return {
        "healthy": health is True or (isinstance(health, dict) and health.get("healthy", False)),
        "details": health if health is not True else None,
    }



@endpoint(
    "/actions/{action_id}/avatar/set_whatsapp_avatar",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Avatar Action"],
)
async def set_whatsapp_avatar(action_id: str) -> Dict[str, Any]:
    """Set the WhatsApp profile picture using the current local avatar image."""
    action = await _get_avatar_action(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Avatar action with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    try:
        success = await action.set_whatsapp_avatar()
        return {
            "success": success,
            "message": "WhatsApp profile picture set successfully" if success else "Failed to set WhatsApp profile picture",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@endpoint(
    "/actions/{action_id}/avatar/pull_whatsapp_avatar",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Avatar Action"],
)
async def pull_whatsapp_avatar(
    action_id: str, phone: Optional[str] = None
) -> Dict[str, Any]:
    """Pull the profile picture from WhatsApp and save it as the local avatar.

    Args:
        action_id: ID of the Avatar action
        phone: Optional phone number to pull from. If not provided, pulls the agent's own avatar.
    """
    action = await _get_avatar_action(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Avatar action with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    try:
        success = await action.pull_avatar_from_whatsapp(phone=phone)
        return {
            "success": success,
            "message": "Avatar pulled from WhatsApp successfully" if success else "Failed to pull avatar from WhatsApp",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}