"""API endpoints for Avatar action.

This module defines HTTP endpoints for setting, getting and deleting agent avatars.
"""

import logging
from typing import Any, Dict, Optional, Tuple

from jvspatial.api import endpoint
from jvspatial.api.exceptions import ValidationError

from jvagent.action.utils.endpoint_helpers import require_typed_action

from .avatar_action import AvatarAction

logger = logging.getLogger(__name__)


def _parse_image_payload(data: Any) -> Tuple[Optional[str], Optional[str]]:
    """Extract base64 image data and mimetype from body or data URI."""
    image_data = None
    mimetype = None

    if isinstance(data, str) and data.startswith("data:"):
        try:
            header, base64_data = data.split(",", 1)
            mimetype = header.split(";")[0].split(":")[1]
            image_data = base64_data
        except Exception:
            logger.error("Failed to parse data URI string", exc_info=True)
        return image_data, mimetype

    if isinstance(data, dict):
        image_data = data.get("image_data")
        mimetype = data.get("mimetype")
        if not image_data and data:
            for val in data.values():
                if isinstance(val, str) and val.startswith("data:"):
                    try:
                        header, base64_data = val.split(",", 1)
                        mimetype = header.split(";")[0].split(":")[1]
                        image_data = base64_data
                        break
                    except Exception:
                        logger.debug(
                            "Skipping invalid data URI in dict value", exc_info=True
                        )
                        continue

    return image_data, mimetype


@endpoint(
    "/actions/{action_id}/avatar",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Avatar Action"],
    summary="Set agent avatar image",
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
    action = await require_typed_action(
        action_id,
        AvatarAction,
        not_found_message=f"Avatar action with ID '{action_id}' not found",
        wrong_type_message=f"Action '{action_id}' is not an AvatarAction",
    )

    image_data, mimetype = _parse_image_payload(data)
    if not image_data or not mimetype:
        raise ValidationError(
            message=(
                "Could not extract image_data and mimetype. "
                "Provide them explicitly or use a data URI."
            ),
            details={"action_id": action_id},
        )

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
    summary="Get agent avatar image",
)
async def get_avatar(action_id: str) -> Dict[str, Any]:
    """Get the agent's avatar image.

    Args:
        action_id: ID of the Avatar action instance

    Returns:
        Avatar data URI
    """
    action = await require_typed_action(
        action_id,
        AvatarAction,
        not_found_message=f"Avatar action with ID '{action_id}' not found",
        wrong_type_message=f"Action '{action_id}' is not an AvatarAction",
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
    summary="Delete agent avatar image",
)
async def delete_avatar(action_id: str) -> Dict[str, Any]:
    """Delete the agent's avatar image.

    Args:
        action_id: ID of the Avatar action instance

    Returns:
        Status result
    """
    action = await require_typed_action(
        action_id,
        AvatarAction,
        not_found_message=f"Avatar action with ID '{action_id}' not found",
        wrong_type_message=f"Action '{action_id}' is not an AvatarAction",
    )

    success = await action.delete_avatar()

    return {
        "success": success,
        "message": (
            "Avatar deleted successfully" if success else "Failed to delete avatar"
        ),
    }


@endpoint(
    "/actions/{action_id}/avatar/health",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Avatar Action"],
    summary="Avatar action health check",
)
async def avatar_health_check(action_id: str) -> Dict[str, Any]:
    """Check Avatar action health.

    Args:
        action_id: ID of the Avatar action instance

    Returns:
        Health check result
    """
    action = await require_typed_action(
        action_id,
        AvatarAction,
        not_found_message=f"Avatar action with ID '{action_id}' not found",
        wrong_type_message=f"Action '{action_id}' is not an AvatarAction",
    )

    health = await action.healthcheck()

    return {
        "healthy": health is True
        or (isinstance(health, dict) and health.get("healthy", False)),
        "details": health if health is not True else None,
    }


@endpoint(
    "/actions/{action_id}/avatar/set_whatsapp_avatar",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Avatar Action"],
    summary="Set WhatsApp profile picture from stored avatar",
)
async def set_whatsapp_avatar(action_id: str) -> Dict[str, Any]:
    """Set the WhatsApp profile picture using the current local avatar image."""
    action = await require_typed_action(
        action_id,
        AvatarAction,
        not_found_message=f"Avatar action with ID '{action_id}' not found",
        wrong_type_message=f"Action '{action_id}' is not an AvatarAction",
    )

    try:
        success = await action.set_whatsapp_avatar()
        return {
            "success": success,
            "message": (
                "WhatsApp profile picture set successfully"
                if success
                else "Failed to set WhatsApp profile picture"
            ),
        }
    except Exception as e:
        logger.error(
            "set_whatsapp_avatar failed for action_id=%s", action_id, exc_info=True
        )
        raise ValidationError(
            message=str(e),
            details={"action_id": action_id},
        ) from e


@endpoint(
    "/actions/{action_id}/avatar/pull_whatsapp_avatar",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Avatar Action"],
    summary="Pull WhatsApp profile picture into local avatar",
)
async def pull_whatsapp_avatar(
    action_id: str, phone: Optional[str] = None
) -> Dict[str, Any]:
    """Pull the profile picture from WhatsApp and save it as the local avatar.

    Args:
        action_id: ID of the Avatar action
        phone: Optional phone number to pull from. If not provided, pulls the agent's own avatar.
    """
    action = await require_typed_action(
        action_id,
        AvatarAction,
        not_found_message=f"Avatar action with ID '{action_id}' not found",
        wrong_type_message=f"Action '{action_id}' is not an AvatarAction",
    )

    try:
        success = await action.pull_avatar_from_whatsapp(phone=phone)
        return {
            "success": success,
            "message": (
                "Avatar pulled from WhatsApp successfully"
                if success
                else "Failed to pull avatar from WhatsApp"
            ),
        }
    except Exception as e:
        logger.error(
            "pull_whatsapp_avatar failed for action_id=%s", action_id, exc_info=True
        )
        raise ValidationError(
            message=str(e),
            details={"action_id": action_id},
        ) from e
