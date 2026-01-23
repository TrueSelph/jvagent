"""WhatsApp Action Endpoints."""

import asyncio
import logging
import base64
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Request, HTTPException

from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.core.agent import Agent
from jvagent.memory.conversation import Conversation
from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError as APIValidationError
from jvspatial.exceptions import ValidationError, DatabaseError

from .utils.media_manager import MediaManager
from .whatsapp_action import WhatsAppAction

from jvagent.logging.service import INTERACTION_LEVEL_NUMBER
from jvagent.action.interact.endpoints import _build_interaction_log_data
logger = logging.getLogger(__name__)


# Module-level state for batching media messages
# Structure: {sender_id: {media_urls: [], utterances: [], data: {}, timer_task: Task, agent_id: str, action: WhatsAppAction}}
_media_batches: Dict[str, Dict[str, Any]] = {}


async def _process_media_batch(sender: str) -> None:
    """Process accumulated media batch for a user with improved error handling."""
    if sender not in _media_batches:
        return
    
    batch = _media_batches.pop(sender)
    agent_id = batch["agent_id"]
    whatsapp_action = batch["action"]
    
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
        
        # Get conversation with error handling
        try:
            convo_obj = await Conversation.find_one({"context.user_id": sender})
        except DatabaseError as e:
            logger.error(f"Database error finding conversation for user {sender}: {e}")
            return
        
        # Create walker with proper error handling
        try:
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
        except ValidationError as e:
            logger.error(f"Validation error creating walker for user {sender}: {e}")
            return
        
        # Get agent and spawn walker
        try:
            agent = await Agent.get(agent_id)
            if not agent:
                logger.error(f"Agent {agent_id} not found for media batch processing")
                return
                
            await walker.spawn(agent)
        except DatabaseError as e:
            logger.error(f"Database error spawning walker for user {sender}: {e}")
            return
        except Exception as e:
            logger.error(f"Error spawning walker for user {sender}: {e}")
            return
        
        # Finalize interaction with error handling
        interaction = walker.interaction
        if interaction:
            try:
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
                    
            except DatabaseError as e:
                logger.error(f"Database error finalizing interaction for user {sender}: {e}")
            except Exception as e:
                logger.error(f"Error finalizing interaction for user {sender}: {e}")
    
    except Exception as e:
        logger.error(
            f"Error processing batched media for user {sender}: {e}",
            exc_info=True,
        )


async def _schedule_batch_processing(sender: str, delay: float) -> None:
    """Schedule batch processing after a delay."""
    await asyncio.sleep(delay)
    await _process_media_batch(sender)


async def _handle_media_message(
    data, sender: str, agent_id: str, whatsapp_action, utterance: Optional[str]
) -> Dict[str, Any]:
    """Handle media message processing with improved error handling and path safety."""
    try:
        # Trigger typing
        await whatsapp_action.set_typing(sender, value=True, is_group=data.isGroup)
        
        media_manager = MediaManager()
        media_b64 = data.media
        
        if media_b64:
            # Handle potential data: URI prefix
            if "," in media_b64:
                media_b64 = media_b64.split(",")[1]
            
            try:
                media_bytes = base64.b64decode(media_b64)
                
                # Use safe path handling
                media_url = await media_manager.save_media(
                    user_id=sender,
                    media_bytes=media_bytes,
                    mime_type=data.mime_type,
                    filename=data.filename,
                )
                
                if media_url:
                    # Construct safe media URL
                    media_url = whatsapp_action.base_url + media_url
                    logger.info(f"Saved media for user {sender}: {media_url}")
                    
                    # Convert MessagePayload to dict for batching
                    data_dict = _convert_message_payload_to_dict(data)
                    
                    # Handle batching for media messages
                    return _handle_media_batching(
                        sender, media_url, utterance, data_dict, 
                        agent_id, whatsapp_action
                    )
                    
            except (ValueError, base64.binascii.Error) as e:
                logger.error(f"Invalid base64 media data for user {sender}: {e}")
                return {"status": "error", "response": "Invalid media format"}
            except Exception as e:
                logger.error(f"Error processing media for user {sender}: {e}")
                return {"status": "error", "response": "Media processing failed"}
                
    except Exception as e:
        logger.error(f"Error handling media message for user {sender}: {e}", exc_info=True)
        return {"status": "error", "response": "Media handling failed"}
    
    return {"status": "ignored", "response": "No media to process"}


async def _handle_voice_message(data, sender: str, whatsapp_action) -> Dict[str, Any]:
    """Handle voice message (PTT) processing with improved error handling."""
    if not whatsapp_action.stt_action:
        logger.info(f"No STT action configured for WhatsAppAction, ignoring voice message from {sender}")
        return {"status": "ignored", "response": "no stt action configured"}
        
    try:
        # Set status to recording (listening)
        try:
            tts_action = await whatsapp_action.get_action(whatsapp_action.tts_action)
            if tts_action:
                await whatsapp_action.set_recording_status(sender, value=True, is_group=data.isGroup)
            else:
                await whatsapp_action.set_typing(sender, value=True, is_group=data.isGroup)
        except Exception as e:
            logger.warning(f"Failed to set recording/typing status: {e}")

        # Retrieve the STT action
        try:
            stt_action = await whatsapp_action.get_action(whatsapp_action.stt_action)
            if not stt_action:
                logger.warning(f"STT action '{whatsapp_action.stt_action}' not found")
                return {"status": "ignored", "response": "stt action not found"}
        except Exception as e:
            logger.error(f"Error retrieving STT action: {e}")
            return {"status": "error", "response": "STT action retrieval failed"}
            
        # Transcribe with validation
        try:
            if not data.media:
                logger.warning(f"No media data in voice message from {sender}")
                return {"status": "ignored", "response": "no audio data"}
                
            transcript = await stt_action.invoke_base64(audio_base64=data.media)
            
            if transcript and transcript.strip():
                logger.info(f"Transcribed voice message from {sender}: {transcript}")
                return {"status": "transcribed", "transcript": transcript}
            else:
                logger.info(f"Empty transcript for voice message from {sender}")
                return {"status": "ignored", "response": "empty transcript"}
                
        except Exception as e:
            logger.error(f"Error transcribing voice message from {sender}: {e}")
            return {"status": "error", "response": "transcription failed"}
                     
    except Exception as e:
        logger.error(f"Error processing voice message from {sender}: {e}", exc_info=True)
        return {"status": "error", "response": "voice processing failed"}


def _convert_message_payload_to_dict(data) -> Dict[str, Any]:
    """Convert MessagePayload to dict safely."""
    return {
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


def _handle_media_batching(
    sender: str, media_url: str, utterance: Optional[str], 
    data_dict: Dict[str, Any], agent_id: str, whatsapp_action
) -> Dict[str, Any]:
    """Handle media batching logic with improved error handling."""
    try:
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
                _schedule_batch_processing(sender, whatsapp_action.media_batch_window)
            )
            logger.info(f"Added media to existing batch for user {sender}, resetting timer")
        else:
            # Create new batch
            _media_batches[sender] = {
                "media_urls": [media_url],
                "utterances": [utterance],
                "data": data_dict,
                "agent_id": agent_id,
                "action": whatsapp_action,
                "timer_task": None,
            }
            
            # Start timer for batch processing
            _media_batches[sender]["timer_task"] = asyncio.create_task(
                _schedule_batch_processing(sender, whatsapp_action.media_batch_window)
            )
            logger.info(
                f"Created new media batch for user {sender}, "
                f"will process in {whatsapp_action.media_batch_window}s"
            )
        
        # Return immediately - batch will be processed after timer
        return {"status": "received", "response": "media batched"}
        
    except Exception as e:
        logger.error(f"Error handling media batching for user {sender}: {e}", exc_info=True)
        return {"status": "error", "response": "batching failed"}


async def _process_interaction_async(
    data, utterance: str, sender: str, agent_id: str, agent
) -> None:
    """Process the interaction in the background with improved error handling."""
    try:
        # Convert MessagePayload to dict for InteractWalker
        data_dict = _convert_message_payload_to_dict(data)

        # Get conversation with error handling
        try:
            convo_obj = await Conversation.find_one({"context.user_id": sender})
        except DatabaseError as e:
            logger.error(f"Database error finding conversation for user {sender}: {e}")
            return

        # Create walker with proper error handling
        try:
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
        except ValidationError as e:
            logger.error(f"Validation error creating walker for user {sender}: {e}")
            return
        except Exception as e:
            logger.error(f"Error creating walker for user {sender}: {e}")
            return
            
        # Spawn walker with error handling
        try:
            await walker.spawn(agent)
        except Exception as e:
            logger.error(f"Error spawning walker for user {sender}: {e}")
            return

        # Finalize interaction with error handling
        interaction = walker.interaction
        if interaction:
            try:
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
                    
            except DatabaseError as e:
                logger.error(f"Database error finalizing interaction for user {sender}: {e}")
            except Exception as e:
                logger.error(f"Error finalizing interaction for user {sender}: {e}")

    except Exception as e:
        logger.error(
            f"Error processing WhatsApp interaction for agent {agent_id}: {e}",
            exc_info=True,
        )


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
            logger.warning(f"Validation error parsing WhatsApp webhook request: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid request format: {e}")
        except Exception as e:
            logger.warning(f"Error parsing WhatsApp webhook request: {e}")
            data = None


        # logger.info(f"Received WhatsApp webhook for agent {agent_id}: {data}")
        
        if not data or data.fromMe:
            return {"status": "received", "response": "Ignore message"}

        # MessagePayload is a dataclass, access attributes directly
        utterance = data.body or data.caption
        utterance = utterance.strip() if utterance else None
        sender = data.sender

        # Validate sender
        if not sender or "status@broadcast" in data.receiver or sender == data.receiver:
            return {"status": "ignored", "response": "Sender blocked"}
        
        # Check if this is a media message
        if data.message_type in ["image", "document", "video", "audio"] and data.media:
            return await _handle_media_message(data, sender, agent_id, whatsapp_action, utterance)
        elif data.message_type in ["ptt"] and data.media:
            voice_result = await _handle_voice_message(data, sender, whatsapp_action)
            utterance = voice_result.get("transcript", "")
        elif utterance:
            # Trigger typing immediately
            try:
                await whatsapp_action.set_typing(sender, value=True, is_group=data.isGroup)
            except Exception as e:
                logger.warning(f"Failed to set typing status: {e}")
        else:
            return {"status": "ignored", "response": "Ignore interaction"}

        logger.debug(f"Processing utterance: {utterance}")

        # Return immediately with 200 OK
        response = {"status": "received"}

        # Process interaction asynchronously in background
        asyncio.create_task(_process_interaction_async(
            data, utterance, sender, agent_id, agent
        ))

        return response
        
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
    whatsapp_action = await WhatsAppAction.get(action_id)
    if not whatsapp_action or not isinstance(whatsapp_action, WhatsAppAction):
        raise ResourceNotFoundError(f"WhatsApp action not found: {action_id}")

    if outbox:
        logger.warning("Outbox not implemented yet")
        result = {
            "status": "outbox not implemented yet"
        }
    else:
        result = await whatsapp_action.api().send_message(phone=to, message=message, is_group=is_group, is_newsletter=is_newsletter, message_id=message_id, options=options)

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
    whatsapp_action = await WhatsAppAction.get(action_id)
    if not whatsapp_action or not isinstance(whatsapp_action, WhatsAppAction):
        raise ResourceNotFoundError(f"WhatsApp action not found: {action_id}")

    result = await whatsapp_action.api().send_image(
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
    whatsapp_action = await WhatsAppAction.get(action_id)
    if not whatsapp_action or not isinstance(whatsapp_action, WhatsAppAction):
        raise ResourceNotFoundError(f"WhatsApp action not found: {action_id}")

    result = await whatsapp_action.api().send_file(
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
    whatsapp_action = await WhatsAppAction.get(action_id)
    if not whatsapp_action or not isinstance(whatsapp_action, WhatsAppAction):
        raise ResourceNotFoundError(f"WhatsApp action not found: {action_id}")

    result = await whatsapp_action.api().send_voice(
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
    whatsapp_action = await WhatsAppAction.get(action_id)
    if not whatsapp_action or not isinstance(whatsapp_action, WhatsAppAction):
        raise ResourceNotFoundError(f"WhatsApp action not found: {action_id}")

    result = await whatsapp_action.api().send_location(
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
    whatsapp_action = await WhatsAppAction.get(action_id)
    if not whatsapp_action or not isinstance(whatsapp_action, WhatsAppAction):
        raise ResourceNotFoundError(f"WhatsApp action not found: {action_id}")

    result = await whatsapp_action.api().create_group(name=name, participants=participants)

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
    whatsapp_action = await WhatsAppAction.get(action_id)
    if not whatsapp_action or not isinstance(whatsapp_action, WhatsAppAction):
        raise ResourceNotFoundError(f"WhatsApp action not found: {action_id}")

    result = await whatsapp_action.api().add_group_participant(group_id=group_id, phone=phone)

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
    whatsapp_action = await WhatsAppAction.get(action_id)
    if not whatsapp_action or not isinstance(whatsapp_action, WhatsAppAction):
        raise ResourceNotFoundError(f"WhatsApp action not found: {action_id}")

    result = await whatsapp_action.api().remove_group_participant(group_id=group_id, phone=phone)

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
    whatsapp_action = await WhatsAppAction.get(action_id)
    if not whatsapp_action or not isinstance(whatsapp_action, WhatsAppAction):
        raise ResourceNotFoundError(f"WhatsApp action not found: {action_id}")

    result = await whatsapp_action.api().get_profile_picture(phone=phone)

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
    whatsapp_action = await WhatsAppAction.get(action_id)
    if not whatsapp_action or not isinstance(whatsapp_action, WhatsAppAction):
        raise ResourceNotFoundError(f"WhatsApp action not found: {action_id}")

    result = await whatsapp_action.api().status()

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
    whatsapp_action = await WhatsAppAction.get(action_id)
    if not whatsapp_action or not isinstance(whatsapp_action, WhatsAppAction):
        raise ResourceNotFoundError(f"WhatsApp action not found: {action_id}")

    result = await whatsapp_action.api().qrcode()

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
    whatsapp_action = await WhatsAppAction.get(action_id)
    if not whatsapp_action or not isinstance(whatsapp_action, WhatsAppAction):
        raise ResourceNotFoundError(f"WhatsApp action not found: {action_id}")

    result = await whatsapp_action.api().get_host_device()

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
    whatsapp_action = await WhatsAppAction.get(action_id)
    if not whatsapp_action or not isinstance(whatsapp_action, WhatsAppAction):
        raise ResourceNotFoundError(f"WhatsApp action not found: {action_id}")

    result = await whatsapp_action.api().logout()

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
    whatsapp_action = await WhatsAppAction.get(action_id)
    if not whatsapp_action or not isinstance(whatsapp_action, WhatsAppAction):
        raise ResourceNotFoundError(f"WhatsApp action not found: {action_id}")

    result = await whatsapp_action.api().close_session()

    if isinstance(result, dict) and "status" not in result:
        result["status"] = "close" if result.get("success") or result.get("ok") else "failed"

    return result
