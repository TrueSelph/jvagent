"""WhatsApp Action Endpoints."""

import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Request
from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError
from jvspatial.exceptions import DatabaseError, ValidationError

from jvagent.core.agent import Agent

from .utils.endpoint_helpers import (
    _clear_whatsapp_typing,
    _handle_media_message,
    _handle_voice_message,
    _process_interaction_async,
    get_whatsapp_action,
    normalize_result,
)
from .utils.task_helpers import create_background_task

logger = logging.getLogger(__name__)


@endpoint(
    "/whatsapp/interact/webhook/{agent_id}",
    methods=["POST"],
    webhook=True,
    webhook_auth="api_key",  # Validates API key from query param or header
    tags=["WhatsApp"],
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="received"),
            "response": ResponseField(
                field_type=Optional[str], example="Hello!", default=None
            ),
        }
    ),
)
async def whatsapp_interact(request: Request, agent_id: str) -> Dict[str, Any]:
    """WhatsApp Interact Webhook.

    Processes incoming WhatsApp messages and triggers an interaction via InteractWalker.

    AWS Lambda compatibility: By default, the webhook awaits the full interaction
    (including response generation and WhatsApp send) before returning the HTTP response.
    This ensures the interaction completes before Lambda freezes the execution context.

    Set WHATSAPP_WEBHOOK_ASYNC=true to use background task mode (for long-running servers).

    Args:
        request: FastAPI request object
        agent_id: Agent ID from URL path

    Returns:
        Dict containing status and optional response message

    Raises:
        ResourceNotFoundError: If agent or action not found
        HTTPException: For validation errors
    """
    try:
        # Validate agent exists
        agent = await Agent.get(agent_id)
        if not agent:
            raise ResourceNotFoundError(
                message=f"Agent with ID '{agent_id}' not found",
                details={"agent_id": agent_id},
            )

        whatsapp_action = await agent.get_action_by_type("WhatsAppAction")
        if not whatsapp_action:
            raise ResourceNotFoundError(
                message="Action with label 'WhatsAppAction' not found",
                details={"agent_id": agent_id},
            )

        # Parse request data with error handling
        try:
            request_data = await request.json()
            data = await whatsapp_action.api().parse_inbound_message(request_data)
        except ValidationError as e:
            logger.debug(f"Validation error parsing WhatsApp webhook request: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid request format: {e}")
        except Exception as e:
            logger.debug(f"Error parsing WhatsApp webhook request: {e}")
            data = None

        if data.message_type in ["ignored"]:
            return {"status": "ignored", "response": "Ignore message"}

        # logger.warning(f"Received WhatsApp webhook for agent {agent_id}: {data}")

        if not data or data.fromMe:
            return {"status": "received", "response": "Ignore message"}

        # MessagePayload is a dataclass, access attributes directly
        utterance = data.body or data.caption
        utterance = utterance.strip() if utterance else None
        
        if "@" in data.sender:
            data.sender = await whatsapp_action.api().convert_lid_to_phone_number(data.sender)

        sender = data.sender
        sender_name = data.sender_name

        access_control_action = await agent.get_action_by_type("AccessControlAction")
        if access_control_action:
            has_access = await access_control_action.has_action_access(
                session_id=sender, action_label="WhatsAppAction", channel="whatsapp"
            )
            if not has_access:
                return {"status": "received", "response": "Access denied"}

        # Validate sender - ignore status@broadcast messages completely
        if (
            not sender
            or "status@broadcast" in sender
            or "status@broadcast" in data.receiver
            or sender == data.receiver
        ):
            return {"status": "ignored", "response": "Sender blocked"}

        # Check if this is a media message
        if data.message_type in ["image", "document", "video", "audio"] and data.media:
            return await _handle_media_message(
                data, sender, agent_id, whatsapp_action, utterance
            )
        elif data.message_type in ["ptt"] and data.media:
            voice_result = await _handle_voice_message(data, sender, whatsapp_action)
            utterance = voice_result.get("transcript", "")
        elif data.message_type in ["location"] and data.location:
            typing_result = await whatsapp_action.api().set_typing_status(
                phone=sender, value=True, is_group=data.isGroup
            )
            utterance = f"Location: {data.location.get('latitude')}, {data.location.get('longitude')}"
        elif utterance:
            # Trigger typing immediately
            try:
                typing_result = await whatsapp_action.api().set_typing_status(
                    phone=sender, value=True, is_group=data.isGroup
                )
                if not typing_result.get("ok", True):
                    logger.debug(
                        f"Failed to set typing status for {sender}: {typing_result.get('error', 'Unknown error')}"
                    )
            except Exception as e:
                logger.debug(f"Failed to set typing status for {sender}: {e}")
        else:
            await _clear_whatsapp_typing(
                agent, agent_id, sender, getattr(data, "isGroup", False)
            )
            return {"status": "ignored", "response": "Ignore interaction"}

        if utterance and len(utterance) > whatsapp_action.utterance_max_length:
            await _clear_whatsapp_typing(
                agent, agent_id, sender, getattr(data, "isGroup", False)
            )
            return {"status": "ignored", "response": "Utterance too long."}

        # Check if webhook should run in async mode (background task)
        # Default is False (synchronous) for Lambda compatibility
        use_async_mode = (
            os.environ.get("WHATSAPP_WEBHOOK_ASYNC", "false").lower() == "true"
        )

        if use_async_mode:
            # Async mode: Return immediately with 200 OK and process in background
            # Use this mode only for long-running servers, NOT for AWS Lambda
            logger.warning(f"Processing interaction asynchronously for {sender}")
            create_background_task(
                _process_interaction_async(
                    data, utterance, sender, agent_id, agent, sender_name=sender_name
                ),
                name=f"whatsapp_interaction_{sender}",
            )
            return {"status": "received"}
        else:
            # Sync mode (default): Await full interaction before returning
            # This ensures Lambda completes the full flow before freezing
            logger.warning(f"Processing interaction synchronously for {sender}")
            await _process_interaction_async(
                data, utterance, sender, agent_id, agent, sender_name=sender_name
            )
            return {"status": "received"}

    except (ResourceNotFoundError, HTTPException):
        raise
    except DatabaseError as e:
        logger.error(f"Database error in WhatsApp webhook: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error")
    except Exception as e:
        logger.error(f"Unexpected error in WhatsApp webhook: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@endpoint(
    "/actions/{action_id}/send_message",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="sent"),
        }
    ),
)
async def send_message(
    action_id: str,
    to: str,
    message: str,
    is_group: bool = False,
    is_newsletter: bool = False,
    message_id: str = "",
    outbox: bool = False,
    options: Optional[dict] = None,
) -> Dict[str, Any]:
    """Send a WhatsApp message via a specific WhatsApp action.

    Args:
        action_id: ID of the WhatsApp action
        to: Phone number to send message to
        message: Message content
        is_group: Whether the message is for a group
        is_newsletter: Whether the message is a newsletter
        message_id: ID of the message
        options: Additional options

    Returns:
        Dict[str, Any]: Result of the message send operation
    """
    whatsapp_action = await get_whatsapp_action(action_id)

    if outbox:
        logger.debug("Outbox not implemented yet")
        return {"status": "outbox not implemented yet"}

    result = await whatsapp_action.api().send_message(
        phone=to,
        message=message,
        is_group=is_group,
        is_newsletter=is_newsletter,
        message_id=message_id,
        options=options,
    )
    return normalize_result(result, "sent")


@endpoint(
    "/actions/{action_id}/send_image",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="sent"),
        }
    ),
)
async def send_image(
    action_id: str,
    to: str,
    image_url: str,
    caption: str = "",
    filename: str = "image.jpg",
    is_group: bool = False,
) -> Dict[str, Any]:
    """Send a WhatsApp image via a specific WhatsApp action.

    Args:
        action_id: ID of the WhatsApp action
        to: Phone number to send image to
        image_url: URL of the image
        caption: Caption for the image
        filename: Filename for the image
        is_group: Whether the image is for a group

    Returns:
        Dict[str, Any]: Result of the image send operation
    """
    whatsapp_action = await get_whatsapp_action(action_id)
    result = await whatsapp_action.api().send_image(
        phone=to,
        file_url=image_url,
        caption=caption,
        filename=filename,
        is_group=is_group,
    )
    return normalize_result(result, "sent")


@endpoint(
    "/actions/{action_id}/send_file",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="sent"),
        }
    ),
)
async def send_file(
    action_id: str,
    to: str,
    file_url: str,
    caption: str = "",
    filename: str = "file",
    is_group: bool = False,
) -> Dict[str, Any]:
    """Send a WhatsApp file/document via a specific WhatsApp action.

    Args:
        action_id: ID of the WhatsApp action
        to: Phone number to send file to
        file_url: URL of the file
        caption: Caption for the file
        filename: Filename for the file
        is_group: Whether the file is for a group

    Returns:
        Dict[str, Any]: Result of the file send operation
    """
    whatsapp_action = await get_whatsapp_action(action_id)
    result = await whatsapp_action.api().send_file(
        phone=to,
        file_url=file_url,
        caption=caption,
        filename=filename,
        is_group=is_group,
    )
    return normalize_result(result, "sent")


@endpoint(
    "/actions/{action_id}/send_voice",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="sent"),
        }
    ),
)
async def send_voice(
    action_id: str,
    to: str,
    voice_url: str,
    is_group: bool = False,
    quoted_message_id: str = "",
) -> Dict[str, Any]:
    """Send a WhatsApp voice message via a specific WhatsApp action.

    Args:
        action_id: ID of the WhatsApp action
        to: Phone number to send voice to
        voice_url: URL of the voice/audio file
        is_group: Whether the voice is for a group
        quoted_message_id: ID of message to quote/reply to

    Returns:
        Dict[str, Any]: Result of the voice send operation
    """
    whatsapp_action = await get_whatsapp_action(action_id)
    result = await whatsapp_action.api().send_voice(
        phone=to,
        file_url=voice_url,
        is_group=is_group,
        quoted_message_id=quoted_message_id,
    )
    return normalize_result(result, "sent")


@endpoint(
    "/actions/{action_id}/send_location",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="sent"),
        }
    ),
)
async def send_location(
    action_id: str,
    to: str,
    latitude: float,
    longitude: float,
    title: str = "",
    is_group: bool = False,
) -> Dict[str, Any]:
    """Send a WhatsApp location via a specific WhatsApp action.

    Args:
        action_id: ID of the WhatsApp action
        to: Phone number to send location to
        latitude: Latitude coordinate
        longitude: Longitude coordinate
        title: Title/name for the location
        is_group: Whether the location is for a group

    Returns:
        Dict[str, Any]: Result of the location send operation
    """
    whatsapp_action = await get_whatsapp_action(action_id)
    result = await whatsapp_action.api().send_location(
        phone=to, latitude=latitude, longitude=longitude, title=title, is_group=is_group
    )
    return normalize_result(result, "sent")


# ========================================================================
# GROUP MANAGEMENT ENDPOINTS
# ========================================================================


@endpoint(
    "/actions/{action_id}/group/create",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="created"),
        }
    ),
)
async def create_group(
    action_id: str,
    name: str,
    participants: List[str],
) -> Dict[str, Any]:
    """Create a WhatsApp group via a specific WhatsApp action.

    Args:
        action_id: ID of the WhatsApp action
        name: Name of the group
        participants: List of phone numbers to add as participants

    Returns:
        Dict[str, Any]: Result of the group creation
    """
    whatsapp_action = await get_whatsapp_action(action_id)
    result = await whatsapp_action.api().create_group(
        name=name, participants=participants
    )
    return normalize_result(result, "created")


@endpoint(
    "/actions/{action_id}/group/add_participant",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="added"),
        }
    ),
)
async def add_group_participant(
    action_id: str,
    group_id: str,
    phone: str,
) -> Dict[str, Any]:
    """Add a participant to a WhatsApp group.

    Args:
        action_id: ID of the WhatsApp action
        group_id: ID of the group
        phone: Phone number of participant to add

    Returns:
        Dict[str, Any]: Result of the operation
    """
    whatsapp_action = await get_whatsapp_action(action_id)
    result = await whatsapp_action.api().add_group_participant(
        group_id=group_id, phone=phone
    )
    return normalize_result(result, "added")


@endpoint(
    "/actions/{action_id}/group/remove_participant",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="removed"),
        }
    ),
)
async def remove_group_participant(
    action_id: str,
    group_id: str,
    phone: str,
) -> Dict[str, Any]:
    """Remove a participant from a WhatsApp group.

    Args:
        action_id: ID of the WhatsApp action
        group_id: ID of the group
        phone: Phone number of participant to remove

    Returns:
        Dict[str, Any]: Result of the operation
    """
    whatsapp_action = await get_whatsapp_action(action_id)
    result = await whatsapp_action.api().remove_group_participant(
        group_id=group_id, phone=phone
    )
    return normalize_result(result, "removed")


@endpoint(
    "/actions/{action_id}/profile_picture/{phone}",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    response=success_response(
        data={
            "profile_picture": ResponseField(field_type=str, example="https://..."),
        }
    ),
)
async def get_profile_picture(
    action_id: str,
    phone: str,
) -> Dict[str, Any]:
    """Get profile picture URL for a contact.

    Args:
        action_id: ID of the WhatsApp action
        phone: Phone number of the contact

    Returns:
        Dict[str, Any]: Profile picture URL
    """
    whatsapp_action = await get_whatsapp_action(action_id)
    return await whatsapp_action.api().get_profile_picture(phone=phone)


# ========================================================================
# SESSION MANAGEMENT ENDPOINTS
# ========================================================================


@endpoint(
    "/actions/{action_id}/status",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="CONNECTED"),
        }
    ),
)
async def get_session_status(
    action_id: str,
) -> Dict[str, Any]:
    """Get WhatsApp session status.

    Args:
        action_id: ID of the WhatsApp action

    Returns:
        Dict[str, Any]: Session status information
    """
    whatsapp_action = await get_whatsapp_action(action_id)
    return await whatsapp_action.api().status()


@endpoint(
    "/actions/{action_id}/session/register",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="CONNECTED"),
            "ok": ResponseField(field_type=bool, example=True, default=True),
            "message": ResponseField(
                field_type=Optional[str],
                example="Session registered successfully",
                default=None,
            ),
        }
    ),
)
async def register_session(
    action_id: str,
) -> Dict[str, Any]:
    """Register WhatsApp session with the API provider.

    This endpoint is used to manually register or re-register a WhatsApp session,
    particularly useful for:
    - Fresh installs on Lambda where startup registration timed out or didn't run
    - Retrying registration without restarting the app
    - Forcing re-registration after configuration changes

    The endpoint calls register_session() on the WhatsAppAction, which:
    - Generates webhook URL if not set
    - Registers the session with the WhatsApp API provider (WPPConnect, WWebJS, etc.)
    - Returns session status and registration details

    Args:
        action_id: ID of the WhatsApp action

    Returns:
        Dict[str, Any]: Registration result with status, ok flag, and message
    """
    whatsapp_action = await get_whatsapp_action(action_id)
    result = await whatsapp_action.register_session()

    # If registration succeeded, mark as registered to avoid redundant lazy calls
    if isinstance(result, dict):
        if result.get("ok", True) and result.get("status") != "ERROR":
            whatsapp_action._session_registered = True

    return result


@endpoint(
    "/actions/{action_id}/qrcode",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    response=success_response(
        data={
            "qrcode": ResponseField(
                field_type=str, example="data:image/png;base64,..."
            ),
        }
    ),
)
async def get_qrcode(
    action_id: str,
) -> Dict[str, Any]:
    """Get QR code for WhatsApp authentication.

    Args:
        action_id: ID of the WhatsApp action

    Returns:
        Dict[str, Any]: QR code as base64 image
    """
    whatsapp_action = await get_whatsapp_action(action_id)
    return await whatsapp_action.api().qrcode()


@endpoint(
    "/actions/{action_id}/device",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    response=success_response(
        data={
            "device": ResponseField(field_type=dict, example={}),
        }
    ),
)
async def get_device_info(
    action_id: str,
) -> Dict[str, Any]:
    """Get connected device information.

    Args:
        action_id: ID of the WhatsApp action

    Returns:
        Dict[str, Any]: Device information
    """
    whatsapp_action = await get_whatsapp_action(action_id)
    return await whatsapp_action.api().get_host_device()


@endpoint(
    "/actions/{action_id}/logout",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="logout"),
        }
    ),
)
async def logout(
    action_id: str,
) -> Dict[str, Any]:
    """Logout from WhatsApp.

    Args:
        action_id: ID of the WhatsApp action

    Returns:
        Dict[str, Any]: Result of the operation
    """
    whatsapp_action = await get_whatsapp_action(action_id)
    result = await whatsapp_action.api().logout_session()
    return normalize_result(result, "logout")


@endpoint(
    "/actions/{action_id}/close",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="close"),
        }
    ),
)
async def close(
    action_id: str,
) -> Dict[str, Any]:
    """Close WhatsApp session.

    Args:
        action_id: ID of the WhatsApp action

    Returns:
        Dict[str, Any]: Result of the operation
    """
    whatsapp_action = await get_whatsapp_action(action_id)
    result = await whatsapp_action.api().close_session()
    return normalize_result(result, "close")
