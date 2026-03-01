"""Helper functions for WhatsApp endpoint processing.

This module contains helper functions used by WhatsApp endpoints for processing
messages, creating walkers, handling media, and managing interactions.
"""

import base64
import logging
from typing import Any, Dict, Optional

from jvspatial.api.exceptions import ResourceNotFoundError
from jvspatial.exceptions import DatabaseError, ValidationError

from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.interact.utils import flush_deferred_saves
from jvagent.core.agent import Agent
from jvagent.core.app import App
from jvagent.memory.conversation import Conversation

from ..whatsapp_action import WhatsAppAction
from .conversation_lock_manager import ConversationLockManager
from .media_batch_manager import MediaBatchManager
from .media_manager import MediaManager

logger = logging.getLogger(__name__)

# Global instances
_batch_manager = MediaBatchManager()
_conversation_lock_manager = ConversationLockManager()


async def get_whatsapp_action(action_id: str) -> WhatsAppAction:
    """Get and validate a WhatsApp action by ID.

    Args:
        action_id: ID of the WhatsApp action

    Returns:
        WhatsAppAction instance

    Raises:
        ResourceNotFoundError: If action not found or wrong type
    """
    whatsapp_action = await WhatsAppAction.get(action_id)
    if not whatsapp_action or not isinstance(whatsapp_action, WhatsAppAction):
        raise ResourceNotFoundError(f"WhatsApp action not found: {action_id}")
    return whatsapp_action


def normalize_result(
    result: Dict[str, Any], default_status: str = "sent"
) -> Dict[str, Any]:
    """Normalize API result by adding status field if missing.

    Args:
        result: API response dictionary
        default_status: Default status to use on success

    Returns:
        Result dict with normalized status
    """
    if isinstance(result, dict) and "status" not in result:
        result["status"] = (
            default_status if result.get("success") or result.get("ok") else "failed"
        )
    return result


async def _store_whatsapp_metadata_in_interaction(
    walker: InteractWalker, data_dict: Dict[str, Any]
) -> None:
    """Store WhatsApp-specific metadata in interaction for adapter retrieval.

    Args:
        walker: InteractWalker instance with created interaction
        data_dict: WhatsApp message data dictionary
    """
    if not walker.interaction or not data_dict:
        return

    try:
        # Extract WhatsApp-specific fields and store as channel metadata event
        whatsapp_metadata = {}
        if "isGroup" in data_dict:
            whatsapp_metadata["isGroup"] = data_dict["isGroup"]
        if "sender" in data_dict:
            whatsapp_metadata["sender"] = data_dict["sender"]
        if "message_id" in data_dict:
            whatsapp_metadata["message_id"] = data_dict["message_id"]

        if whatsapp_metadata:
            channel_metadata_event = {
                "action_name": "WhatsAppAction",
                "content": "channel_metadata:whatsapp",
                "data": whatsapp_metadata,
            }
            walker.interaction.events.append(channel_metadata_event)
            # Save interaction to persist the metadata
            await walker.interaction.save()
    except Exception as e:
        logger.debug(f"Failed to store WhatsApp metadata in interaction: {e}")


async def get_conversation_with_lock(sender: str) -> Optional[Any]:
    """Get conversation for user with proper locking to prevent duplicates.

    This function ensures that only one request at a time can look up or
    create a conversation for a given user, preventing race conditions.

    Args:
        sender: User ID / phone number

    Returns:
        Conversation object if found, None otherwise
    """
    lock = await _conversation_lock_manager.acquire_lock(sender)

    async with lock:
        try:
            return await Conversation.find_one({"context.user_id": sender})
        except DatabaseError as e:
            logger.error(f"Database error finding conversation for user {sender}: {e}")
            return None


async def create_whatsapp_walker(
    agent_id: str,
    utterance: str,
    sender: str,
    data_dict: Dict[str, Any],
    sender_name: Optional[str] = None,
) -> Optional[InteractWalker]:
    """Create an InteractWalker for WhatsApp interactions.

    Uses conversation locking to get session_id if available.

    Args:
        agent_id: Agent ID to interact with
        utterance: User's message
        sender: User ID / phone number
        data_dict: Additional data for the walker

    Returns:
        InteractWalker instance or None on error
    """
    try:
        # Get conversation with locking to prevent duplicates
        convo_obj = await get_conversation_with_lock(sender)

        if convo_obj and getattr(convo_obj, "session_id", None):
            return InteractWalker(
                agent_id=agent_id,
                utterance=utterance,
                channel="whatsapp",
                data=data_dict,
                session_id=convo_obj.session_id,
                user_name=sender_name,
                stream=False,  # WhatsApp uses non-streaming mode
            )
        else:
            return InteractWalker(
                agent_id=agent_id,
                utterance=utterance,
                channel="whatsapp",
                data=data_dict,
                user_id=sender,
                user_name=sender_name,
                stream=False,  # WhatsApp uses non-streaming mode
            )
    except ValidationError as e:
        logger.error(f"Validation error creating walker for user {sender}: {e}")
        return None
    except Exception as e:
        logger.error(f"Error creating walker for user {sender}: {e}")
        return None


async def finalize_whatsapp_interaction(
    walker: InteractWalker,
    agent_id: str,
    sender: str,
) -> None:
    """Finalize a WhatsApp interaction after walker execution.

    Handles response bus finalization, interaction closing, saving, and logging.

    Args:
        walker: The executed InteractWalker
        agent_id: Agent ID for logging
        sender: User ID for error logging
    """
    interaction = walker.interaction
    if not interaction:
        return

    try:
        await interaction.close_interaction()

        # Flush deferred saves (interaction and conversation) with error handling
        await flush_deferred_saves(interaction, walker.conversation)

        # Compute usage after flush so all model_call events are present
        from jvagent.action.interact.endpoints import _finalize_usage

        await _finalize_usage(interaction)

        # Log interaction
        try:
            from jvagent.action.interact.endpoints import _build_interaction_log_data
            from jvagent.logging.service import INTERACTION_LEVEL_NUMBER

            app = await App.get()
            if app:
                active_tasks = []
                if walker.conversation:
                    active_tasks = walker.conversation.get_active_tasks(
                        status="active"
                    )
                log_data, message = _build_interaction_log_data(
                    interaction, app.id, agent_id, active_tasks=active_tasks
                )
                logger.log(INTERACTION_LEVEL_NUMBER, message, extra=log_data)
        except Exception as log_err:
            logger.debug(f"Failed to log WhatsApp interaction: {log_err}")

    except DatabaseError as e:
        logger.error(f"Database error finalizing interaction for user {sender}: {e}")
    except Exception as e:
        logger.error(f"Error finalizing interaction for user {sender}: {e}")


async def _clear_whatsapp_typing(
    agent: Any, agent_id: str, sender: str, is_group: bool = False
) -> None:
    """Clear typing indicator for WhatsApp. Safe to call multiple times."""
    try:
        if not agent:
            return
        whatsapp_action = await agent.get_action_by_type("WhatsAppAction")
        if whatsapp_action and whatsapp_action.is_configured():
            await whatsapp_action.api().set_typing_status(
                phone=sender, value=False, is_group=is_group
            )
    except Exception as e:
        logger.debug(f"Failed to clear typing status for {sender}: {e}")


def _convert_message_payload_to_dict(data: Any) -> Dict[str, Any]:
    """Convert MessagePayload to dict safely.

    Args:
        data: MessagePayload object

    Returns:
        Dictionary representation of the message payload
    """
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


async def _handle_media_batching(
    sender: str,
    media_url: str,
    utterance: Optional[str],
    data_dict: Dict[str, Any],
    agent_id: str,
    whatsapp_action: Any,
) -> Dict[str, Any]:
    """Handle media batching logic with thread-safe batch manager.

    Uses the global _batch_manager for concurrent-safe operations.

    Args:
        sender: User ID / phone number
        media_url: URL of the media file
        utterance: Optional text message
        data_dict: Message data dictionary
        agent_id: Agent ID
        whatsapp_action: WhatsAppAction instance

    Returns:
        Dict with status and response
    """
    try:
        return await _batch_manager.get_or_create_batch(
            sender=sender,
            media_url=media_url,
            utterance=utterance,
            data_dict=data_dict,
            agent_id=agent_id,
            whatsapp_action=whatsapp_action,
        )
    except Exception as e:
        logger.error(
            f"Error handling media batching for user {sender}: {e}", exc_info=True
        )
        return {"status": "error", "response": "batching failed"}


async def _handle_media_message(
    data: Any,
    sender: str,
    agent_id: str,
    whatsapp_action: Any,
    utterance: Optional[str],
) -> Dict[str, Any]:
    """Handle media message processing with improved error handling and path safety.

    Args:
        data: MessagePayload object
        sender: User ID / phone number
        agent_id: Agent ID
        whatsapp_action: WhatsAppAction instance
        utterance: Optional text message/caption

    Returns:
        Dict with status and response
    """
    try:
        # Trigger typing
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
                    logger.debug(f"Saved media for user {sender}: {media_url}")

                    # Convert MessagePayload to dict for batching
                    data_dict = _convert_message_payload_to_dict(data)

                    # Handle batching for media messages
                    return await _handle_media_batching(
                        sender,
                        media_url,
                        utterance,
                        data_dict,
                        agent_id,
                        whatsapp_action,
                    )

            except (ValueError, base64.binascii.Error) as e:
                logger.error(f"Invalid base64 media data for user {sender}: {e}")
                return {"status": "error", "response": "Invalid media format"}
            except Exception as e:
                logger.error(f"Error processing media for user {sender}: {e}")
                return {"status": "error", "response": "Media processing failed"}

    except Exception as e:
        logger.error(
            f"Error handling media message for user {sender}: {e}", exc_info=True
        )
        return {"status": "error", "response": "Media handling failed"}

    return {"status": "ignored", "response": "No media to process"}


async def _handle_voice_message(
    data: Any, sender: str, whatsapp_action: Any
) -> Dict[str, Any]:
    """Handle voice message (PTT) processing with improved error handling.

    Args:
        data: MessagePayload object
        sender: User ID / phone number
        whatsapp_action: WhatsAppAction instance

    Returns:
        Dict with status and optional transcript
    """
    if not whatsapp_action.stt_action:
        logger.debug(
            f"No STT action configured for WhatsAppAction, ignoring voice message from {sender}"
        )
        return {"status": "ignored", "response": "no stt action configured"}

    try:
        # Set status to recording (listening)
        try:
            tts_action = await whatsapp_action.get_action(whatsapp_action.tts_action)
            if tts_action:
                await whatsapp_action.set_recording_status(
                    sender, value=True, is_group=data.isGroup
                )
            else:
                typing_result = await whatsapp_action.api().set_typing_status(
                    phone=sender, value=True, is_group=data.isGroup
                )
                if not typing_result.get("ok", True):
                    logger.debug(
                        f"Failed to set typing status for {sender}: {typing_result.get('error', 'Unknown error')}"
                    )
        except Exception as e:
            logger.debug(f"Failed to set recording/typing status for {sender}: {e}")

        # Retrieve the STT action
        try:
            stt_action = await whatsapp_action.get_action(whatsapp_action.stt_action)
            if not stt_action:
                logger.debug(f"STT action '{whatsapp_action.stt_action}' not found")
                return {"status": "ignored", "response": "stt action not found"}
        except Exception as e:
            logger.error(f"Error retrieving STT action: {e}")
            return {"status": "error", "response": "STT action retrieval failed"}

        # Transcribe with validation
        try:
            if not data.media:
                logger.debug(f"No media data in voice message from {sender}")
                return {"status": "ignored", "response": "no audio data"}

            transcript = await stt_action.invoke_base64(audio_base64=data.media)

            if transcript and transcript.strip():
                logger.debug(f"Transcribed voice message from {sender}: {transcript}")
                return {"status": "transcribed", "transcript": transcript}
            else:
                logger.debug(f"Empty transcript for voice message from {sender}")
                return {"status": "ignored", "response": "empty transcript"}

        except Exception as e:
            logger.error(f"Error transcribing voice message from {sender}: {e}")
            return {"status": "error", "response": "transcription failed"}

    except Exception as e:
        logger.error(
            f"Error processing voice message from {sender}: {e}", exc_info=True
        )
        return {"status": "error", "response": "voice processing failed"}


async def _process_interaction_async(
    data: Any,
    utterance: str,
    sender: str,
    agent_id: str,
    agent: Any,
    sender_name: Optional[str] = None,
) -> None:
    """Process the interaction in the background with improved error handling.

    Uses conversation locking to prevent race conditions when multiple
    messages from the same user arrive simultaneously.

    Lambda compatibility: Ensures WhatsApp adapter is registered before processing
    (lazy initialization for cold starts).

    Args:
        data: MessagePayload object
        utterance: User's message text
        sender: User ID / phone number
        agent_id: Agent ID
        agent: Agent instance
    """
    try:
        # Ensure WhatsApp adapter is registered (lazy init for Lambda cold start)
        whatsapp_action = await agent.get_action_by_type("WhatsAppAction")
        if whatsapp_action:
            adapter_ready = await whatsapp_action.ensure_adapter_registered()
            if not adapter_ready:
                logger.warning(
                    f"WhatsApp adapter not ready for agent {agent_id}. "
                    "Message processing may fail."
                )
        else:
            logger.warning(f"WhatsAppAction not found for agent {agent_id}")
    except Exception as e:
        logger.error(f"Error ensuring adapter registration for agent {agent_id}: {e}")
        # Continue anyway - adapter might still work if already registered

    is_group = getattr(data, "isGroup", False)
    try:
        # Convert MessagePayload to dict for InteractWalker
        data_dict = _convert_message_payload_to_dict(data)
        is_group = is_group or data_dict.get("isGroup", False)

        # Create walker using helper function
        walker = await create_whatsapp_walker(
            agent_id, utterance, sender, data_dict, sender_name=sender_name
        )
        if not walker:
            return

        # Spawn walker with error handling
        try:
            await walker.spawn(agent)
        except Exception as e:
            logger.error(f"Error spawning walker for user {sender}: {e}")
            return

        # Store WhatsApp-specific metadata in interaction for adapter retrieval
        await _store_whatsapp_metadata_in_interaction(walker, data_dict)

        # Finalize interaction using helper function
        await finalize_whatsapp_interaction(walker, agent_id, sender)

    except Exception as e:
        logger.error(
            f"Error processing WhatsApp interaction for agent {agent_id}: {e}",
            exc_info=True,
        )
    finally:
        await _clear_whatsapp_typing(agent, agent_id, sender, is_group)
