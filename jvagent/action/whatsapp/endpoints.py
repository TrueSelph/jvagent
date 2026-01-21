"""WhatsApp Action Endpoints."""

import asyncio
import logging
import base64
from typing import Any, Dict, List, Optional

from fastapi import Request

from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.core.agent import Agent
from jvagent.memory.conversation import Conversation
from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError

from .utils.media_manager import MediaManager
from .whatsapp_action import WhatsAppAction

from jvagent.logging.service import INTERACTION_LEVEL_NUMBER
from jvagent.action.interact.endpoints import _build_interaction_log_data
logger = logging.getLogger(__name__)


# Module-level state for batching media messages
# Structure: {sender_id: {media_urls: [], utterances: [], data: {}, timer_task: Task, agent_id: str, action: WhatsAppAction}}
_media_batches: Dict[str, Dict[str, Any]] = {}


async def _process_media_batch(sender: str) -> None:
    """Process accumulated media batch for a user."""
    if sender not in _media_batches:
        return
    
    batch = _media_batches.pop(sender)
    agent_id = batch["agent_id"]
    action = batch["action"]
    
    try:
        
        # Combine all media URLs
        all_media = batch["media_urls"]
        
        # Combine utterances or use default
        utterances = [u for u in batch["utterances"] if u]
        combined_utterance = " | ".join(utterances) if utterances else "I've attached media"
        
        # Use the data from the first message and add all media
        data = batch["data"]
        data["whatsapp_media"] = all_media
        
        logger.info(f"Processing batched media for user {sender}: {len(all_media)} items")
        
        # Get conversation
        convo_obj = await Conversation.find_one({"context.user_id": sender})
        
        # Create walker
        if convo_obj and getattr(convo_obj, "session_id", None):
            walker = InteractWalker(
                agent_id=agent_id,
                utterance=combined_utterance,
                channel="whatsapp",
                data=data,
                session_id=convo_obj.session_id,
                stream=False,
            )
        else:
            walker = InteractWalker(
                agent_id=agent_id,
                utterance=combined_utterance,
                channel="whatsapp",
                data=data,
                user_id=sender,
                stream=False,
            )
        
        # Get agent and spawn walker
        agent = await Agent.get(agent_id)
        await walker.spawn(agent)
        
        # Finalize interaction
        interaction = walker.interaction
        if interaction:
            if walker.response_bus:
                await walker.response_bus.finalize_interaction(
                    interaction_id=interaction.id,
                    interaction=interaction,
                    session_id=walker.session_id or "",
                    channel=walker.channel,
                )
            
            interaction.close_interaction()
            await interaction.save()
            
            # Log interaction
            try:
                from jvagent.core.app import App
                app = await App.get()
                if app:
                    log_data, message = _build_interaction_log_data(
                        interaction, app.id, agent_id
                    )
                    logger.log(INTERACTION_LEVEL_NUMBER, message, extra=log_data)
            except Exception as log_err:
                logger.warning(f"Failed to log WhatsApp interaction: {log_err}")
    
    except Exception as e:
        logger.error(
            f"Error processing batched media for user {sender}: {e}",
            exc_info=True,
        )


async def _schedule_batch_processing(sender: str, delay: float) -> None:
    """Schedule batch processing after a delay."""
    await asyncio.sleep(delay)
    await _process_media_batch(sender)


@endpoint(
    "/whatsapp/interact/webhook/{agent_id}",
    methods=["POST"],
    webhook=True,
    webhook_auth="api_key",  # Validates API key from query param or header
    tags=["WhatsApp"],
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="received"),
            "response": ResponseField(field_type=Optional[str], example="Hello!", default=None),
        }
    ),
)
async def whatsapp_interact(request: Request, agent_id: str) -> Dict[str, Any]:
    """WhatsApp Interact Webhook.

    Processes incoming WhatsApp messages and triggers an interaction via InteractWalker.
    Returns immediately with 200 OK and processes the interaction asynchronously.
    """

    # Validate agent exists
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID '{agent_id}' not found",
            details={"agent_id": agent_id},
        )

    action = await agent.get_action_by_type("WhatsAppAction")
    if not action:
        raise ResourceNotFoundError(
            message="Action with label 'WhatsAppAction' not found",
            details={"agent_id": agent_id},
        )

    try:
        request_data = await request.json()
        data = await action.api().parse_inbound_message(request_data)
    except Exception as e:
        logger.warning(f"Error parsing WhatsApp webhook request: {e}")
        data = None

    # logger.info(f"Received WhatsApp webhook for agent {agent_id}: {data}")

    logger.info(f"Received WhatsApp webhook for agent {agent_id}: {data}")
    if not data or data.fromMe:
        return {"status": "received", "response": "Ignore message"}

    # MessagePayload is a dataclass, access attributes directly
    utterance = data.body or data.caption
    utterance = utterance.strip() if utterance else None
    sender = data.sender

    
    # Check if this is a media message
    if data.message_type in ["image", "document", "video", "audio"]:
        # Trigger typing
        await action.set_typing(phone=sender, value=True, is_group=data.isGroup)
        
        media_manager = MediaManager()
        media_b64 = data.media
        
        if media_b64:
            # Handle potential data: URI prefix
            if "," in media_b64:
                media_b64 = media_b64.split(",")[1]
            
            try:
                media_bytes = base64.b64decode(media_b64)
                media_url = await media_manager.save_media(
                    user_id=sender,
                    media_bytes=media_bytes,
                    mime_type=data.mime_type,
                    filename=data.filename,
                )
                
                if media_url:
                    media_url = action.base_url + media_url
                    logger.info(f"Saved media for user {sender}: {media_url}")
                    
                    # Convert MessagePayload to dict for batching
                    data_dict = {
                        "message_id": data.message_id,
                        "event_type": data.event_type,
                        "message_type": data.message_type,
                        "author": data.author,
                        "sender": data.sender,
                        "receiver": data.receiver,
                        "caption": data.caption,
                        "location": data.location,
                        "fromMe": data.fromMe,
                        "isGroup": data.isGroup,
                        "isForwarded": data.isForwarded,
                        "sender_name": data.sender_name,
                        "mentionedIds": data.mentionedIds,
                        "body": data.body,
                        "media": data.media,
                        "filename": data.filename,
                        "mime_type": data.mime_type,
                        "quoted_message": data.quoted_message,
                        "contact": data.contact,
                        "poll_id": data.poll_id,
                        "selectedOptions": data.selectedOptions,
                    }
                    
                    # Handle batching for media messages
                    if sender in _media_batches:
                        # Add to existing batch
                        batch = _media_batches[sender]
                        batch["media_urls"].append(media_url)
                        batch["utterances"].append(utterance)
                        
                        # Cancel existing timer and start a new one
                        if batch.get("timer_task"):
                            batch["timer_task"].cancel()
                        
                        batch["timer_task"] = asyncio.create_task(
                            _schedule_batch_processing(sender, action.media_batch_window)
                        )
                        logger.info(f"Added media to existing batch for user {sender}, resetting timer")
                    else:
                        # Create new batch
                        _media_batches[sender] = {
                            "media_urls": [media_url],
                            "utterances": [utterance],
                            "data": data_dict,
                            "agent_id": agent_id,
                            "action": action,
                            "timer_task": None,
                        }
                        
                        # Start timer for batch processing
                        _media_batches[sender]["timer_task"] = asyncio.create_task(
                            _schedule_batch_processing(sender, action.media_batch_window)
                        )
                        logger.info(
                            f"Created new media batch for user {sender}, "
                            f"will process in {action.media_batch_window}s"
                        )
                    
                    # Return immediately - batch will be processed after timer
                    return {"status": "received", "response": "media batched"}
                    
            except Exception as e:
                logger.error(f"Error processing media for user {sender}: {e}")

    elif data.message_type in ["ptt"]:
        # Handle voice messages (PTT)
        if action.stt_action:
            try:
                # Set status to recording (listening)
                await action.set_recording_status(
                    phone=sender, value=True, is_group=data.isGroup, duration=10
                )
                
                # Retrieve the STT action
                stt_action = await action.get_action(action.stt_action)
                if stt_action:
                    # Decode audio
                    audio_b64 = data.media
                    if "," in audio_b64:
                        audio_b64 = audio_b64.split(",")[1]
                    
                    audio_bytes = base64.b64decode(audio_b64)
                    
                    # Transcribe
                    transcript = await stt_action.transcribe(audio_bytes)
                    
                    if transcript:
                        logger.info(f"Transcribed voice message from {sender}: {transcript}")
                        utterance = transcript
                    else:
                        logger.warning(f"Empty transcript for voice message from {sender}")
                        return {"status": "ignored", "response": "empty transcript"}
                else:
                     logger.warning(f"STT action '{action.stt_action}' not found")
                     return {"status": "ignored", "response": "stt action not found"}
                     
            except Exception as e:
                logger.error(f"Error processing voice message from {sender}: {e}")
                return {"status": "error", "response": str(e)}
        else:
             logger.info(f"No STT action configured for WhatsAppAction, ignoring voice message from {sender}")
             return {"status": "ignored", "response": "no stt action configured"}

        
    # For non-media messages, process immediately
    if not utterance:
        return {"status": "ignored", "response": "no utterance found"}

    logger.warning(f"#################################################")
    logger.warning(f"Utterance: {utterance}")

    # Return immediately with 200 OK
    response = {"status": "received"}

    # Process interaction asynchronously in background
    async def process_interaction():

        """Process the interaction in the background."""
        try:
            # Trigger typing immediately
            await action.set_typing(phone=sender, value=True, is_group=data.isGroup)

            # Convert MessagePayload to dict for InteractWalker
            data_dict = {
                "message_id": data.message_id,
                "event_type": data.event_type,
                "message_type": data.message_type,
                "author": data.author,
                "sender": data.sender,
                "receiver": data.receiver,
                "caption": data.caption,
                "location": data.location,
                "fromMe": data.fromMe,
                "isGroup": data.isGroup,
                "isForwarded": data.isForwarded,
                "sender_name": data.sender_name,
                "mentionedIds": data.mentionedIds,
                "body": data.body,
                "media": data.media,
                "filename": data.filename,
                "mime_type": data.mime_type,
                "quoted_message": data.quoted_message,
                "contact": data.contact,
                "poll_id": data.poll_id,
                "selectedOptions": data.selectedOptions,
            }

            convo_obj = await Conversation.find_one({"context.user_id": sender})

            if convo_obj and getattr(convo_obj, "session_id", None):
                walker = InteractWalker(
                    agent_id=agent_id,
                    utterance=utterance,
                    channel="whatsapp",
                    data=data_dict,
                    session_id=convo_obj.session_id,
                    stream=False,
                )
            else:
                walker = InteractWalker(
                    agent_id=agent_id,
                    utterance=utterance,
                    channel="whatsapp",
                    data=data_dict,
                    user_id=sender,
                    stream=False,
                )
            await walker.spawn(agent)

            # Finalize interaction
            interaction = walker.interaction
            if interaction:
                if walker.response_bus:
                    await walker.response_bus.finalize_interaction(
                        interaction_id=interaction.id,
                        interaction=interaction,
                        session_id=walker.session_id or "",
                        channel=walker.channel,
                    )

                interaction.close_interaction()
                await interaction.save()

                # Log interaction
                try:
                    from jvagent.core.app import App
                    app = await App.get()
                    if app:
                        log_data, message = _build_interaction_log_data(
                            interaction, app.id, agent_id
                        )
                        logger.log(INTERACTION_LEVEL_NUMBER, message, extra=log_data)
                except Exception as log_err:
                    logger.warning(f"Failed to log WhatsApp interaction: {log_err}")

        except Exception as e:
            logger.error(
                f"Error processing WhatsApp interaction for agent {agent_id}: {e}",
                exc_info=True,
            )

    # Start background task
    asyncio.create_task(process_interaction())

    return response


@endpoint(
    "/actions/{action_id}/send_message",
    methods=["POST"],
    auth=True,
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
    action = await WhatsAppAction.get(action_id)
    if not action or not isinstance(action, WhatsAppAction):
        raise ResourceNotFoundError(f"WhatsApp action not found: {action_id}")

    if outbox:
        logger.warning("Outbox not implemented yet")
        result = {
            "status": "outbox not implemented yet"
        }
    else:
        result = await action.api().send_message(phone=to, message=message, is_group=is_group, is_newsletter=is_newsletter, message_id=message_id, options=options)

    if isinstance(result, dict) and "status" not in result:
        result["status"] = "sent" if result.get("success") or result.get("ok") else "failed"

    return result


@endpoint(
    "/actions/{action_id}/send_image",
    methods=["POST"],
    auth=True,
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
    action = await WhatsAppAction.get(action_id)
    if not action or not isinstance(action, WhatsAppAction):
        raise ResourceNotFoundError(f"WhatsApp action not found: {action_id}")

    result = await action.api().send_image(
        phone=to, 
        file_url=image_url, 
        caption=caption,
        filename=filename,
        is_group=is_group
    )

    if isinstance(result, dict) and "status" not in result:
        result["status"] = "sent" if result.get("success") or result.get("ok") else "failed"

    return result


@endpoint(
    "/actions/{action_id}/send_file",
    methods=["POST"],
    auth=True,
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
    action = await WhatsAppAction.get(action_id)
    if not action or not isinstance(action, WhatsAppAction):
        raise ResourceNotFoundError(f"WhatsApp action not found: {action_id}")

    result = await action.api().send_file(
        phone=to, 
        file_url=file_url, 
        caption=caption,
        filename=filename,
        is_group=is_group
    )

    if isinstance(result, dict) and "status" not in result:
        result["status"] = "sent" if result.get("success") or result.get("ok") else "failed"

    return result


@endpoint(
    "/actions/{action_id}/send_voice",
    methods=["POST"],
    auth=True,
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
    action = await WhatsAppAction.get(action_id)
    if not action or not isinstance(action, WhatsAppAction):
        raise ResourceNotFoundError(f"WhatsApp action not found: {action_id}")

    result = await action.api().send_voice(
        phone=to, 
        file_url=voice_url, 
        is_group=is_group,
        quoted_message_id=quoted_message_id
    )

    if isinstance(result, dict) and "status" not in result:
        result["status"] = "sent" if result.get("success") or result.get("ok") else "failed"

    return result


@endpoint(
    "/actions/{action_id}/send_location",
    methods=["POST"],
    auth=True,
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
    action = await WhatsAppAction.get(action_id)
    if not action or not isinstance(action, WhatsAppAction):
        raise ResourceNotFoundError(f"WhatsApp action not found: {action_id}")

    result = await action.api().send_location(
        phone=to, 
        latitude=latitude,
        longitude=longitude,
        title=title,
        is_group=is_group
    )

    if isinstance(result, dict) and "status" not in result:
        result["status"] = "sent" if result.get("success") or result.get("ok") else "failed"

    return result


# ========================================================================
# GROUP MANAGEMENT ENDPOINTS
# ========================================================================

@endpoint(
    "/actions/{action_id}/group/create",
    methods=["POST"],
    auth=True,
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
    action = await WhatsAppAction.get(action_id)
    if not action or not isinstance(action, WhatsAppAction):
        raise ResourceNotFoundError(f"WhatsApp action not found: {action_id}")

    result = await action.api().create_group(name=name, participants=participants)

    if isinstance(result, dict) and "status" not in result:
        result["status"] = "created" if result.get("success") or result.get("ok") else "failed"

    return result


@endpoint(
    "/actions/{action_id}/group/add_participant",
    methods=["POST"],
    auth=True,
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
    action = await WhatsAppAction.get(action_id)
    if not action or not isinstance(action, WhatsAppAction):
        raise ResourceNotFoundError(f"WhatsApp action not found: {action_id}")

    result = await action.api().add_group_participant(group_id=group_id, phone=phone)

    if isinstance(result, dict) and "status" not in result:
        result["status"] = "added" if result.get("success") or result.get("ok") else "failed"

    return result


@endpoint(
    "/actions/{action_id}/group/remove_participant",
    methods=["POST"],
    auth=True,
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
    action = await WhatsAppAction.get(action_id)
    if not action or not isinstance(action, WhatsAppAction):
        raise ResourceNotFoundError(f"WhatsApp action not found: {action_id}")

    result = await action.api().remove_group_participant(group_id=group_id, phone=phone)

    if isinstance(result, dict) and "status" not in result:
        result["status"] = "removed" if result.get("success") or result.get("ok") else "failed"

    return result


@endpoint(
    "/actions/{action_id}/profile_picture/{phone}",
    methods=["GET"],
    auth=True,
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
    action = await WhatsAppAction.get(action_id)
    if not action or not isinstance(action, WhatsAppAction):
        raise ResourceNotFoundError(f"WhatsApp action not found: {action_id}")

    result = await action.api().get_profile_picture(phone=phone)

    return result


# ========================================================================
# SESSION MANAGEMENT ENDPOINTS
# ========================================================================

@endpoint(
    "/actions/{action_id}/status",
    methods=["GET"],
    auth=True,
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
    action = await WhatsAppAction.get(action_id)
    if not action or not isinstance(action, WhatsAppAction):
        raise ResourceNotFoundError(f"WhatsApp action not found: {action_id}")

    result = await action.api().status()

    return result


@endpoint(
    "/actions/{action_id}/qrcode",
    methods=["GET"],
    auth=True,
    tags=["WhatsApp"],
    response=success_response(
        data={
            "qrcode": ResponseField(field_type=str, example="data:image/png;base64,..."),
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
    action = await WhatsAppAction.get(action_id)
    if not action or not isinstance(action, WhatsAppAction):
        raise ResourceNotFoundError(f"WhatsApp action not found: {action_id}")

    result = await action.api().qrcode()

    return result


@endpoint(
    "/actions/{action_id}/device",
    methods=["GET"],
    auth=True,
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
    action = await WhatsAppAction.get(action_id)
    if not action or not isinstance(action, WhatsAppAction):
        raise ResourceNotFoundError(f"WhatsApp action not found: {action_id}")

    result = await action.api().get_host_device()

    return result

@endpoint(
    "/actions/{action_id}/logout",
    methods=["POST"],
    auth=True,
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
    action = await WhatsAppAction.get(action_id)
    if not action or not isinstance(action, WhatsAppAction):
        raise ResourceNotFoundError(f"WhatsApp action not found: {action_id}")

    result = await action.api().logout()

    if isinstance(result, dict) and "status" not in result:
        result["status"] = "logout" if result.get("success") or result.get("ok") else "failed"

    return result

@endpoint(
    "/actions/{action_id}/close",
    methods=["POST"],
    auth=True,
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
    action = await WhatsAppAction.get(action_id)
    if not action or not isinstance(action, WhatsAppAction):
        raise ResourceNotFoundError(f"WhatsApp action not found: {action_id}")

    result = await action.api().close_session()

    if isinstance(result, dict) and "status" not in result:
        result["status"] = "close" if result.get("success") or result.get("ok") else "failed"

    return result
