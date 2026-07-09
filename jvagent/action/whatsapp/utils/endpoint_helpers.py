"""Helper functions for WhatsApp endpoint processing.

This module contains helper functions used by WhatsApp endpoints for processing
messages, creating walkers, handling media, and managing interactions.
"""

import asyncio
import base64
import logging
import re
from typing import Any, Dict, Optional, Tuple

from jvspatial.api.exceptions import ResourceNotFoundError
from jvspatial.exceptions import DatabaseError, ValidationError

from jvagent.action.channels.media import MediaManager
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.interact.webhook_pipeline import (
    build_utterance_with_quoted_context,
    finalize_interaction_from_webhook,
    get_conversation_with_lock,
)
from jvagent.core.public_url import get_public_base_url

from ..whatsapp_action import WhatsAppAction
from .conversation_lock_manager import ConversationLockManager
from .media_batch_manager import MediaBatchManager

logger = logging.getLogger(__name__)

# Global instances
_batch_manager = MediaBatchManager()


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


def _extract_quoted_image(
    quoted_message: Optional[Dict[str, Any]]
) -> Optional[Dict[str, str]]:
    """Extract base64 image from quoted message when user replies to an image."""
    if not quoted_message or not isinstance(quoted_message, dict):
        return None

    msg_type = (quoted_message.get("type") or "").lower()
    nested = quoted_message.get("message") or {}

    is_image = msg_type in ("image", "img")
    if not is_image and isinstance(nested, dict):
        nested_type = (nested.get("type") or "").lower()
        has_image = "image" in nested or "img" in nested
        is_image = nested_type in ("image", "img") or has_image

    if not is_image:
        return None

    raw = None
    for key in ("body", "data", "media"):
        val = quoted_message.get(key)
        if isinstance(val, str) and len(val) > 100:
            raw = val
            break

    if not raw and isinstance(nested, dict):
        img = nested.get("image") or nested.get("img")
        if isinstance(img, dict):
            for key in ("data", "body", "base64"):
                val = img.get(key)
                if isinstance(val, str) and len(val) > 100:
                    raw = val
                    break
        if not raw:
            for key in ("body", "data"):
                val = nested.get(key)
                if isinstance(val, str) and len(val) > 100:
                    raw = val
                    break

    if not raw or not raw.strip():
        return None

    s = raw.strip()
    if "," in s and s.lower().startswith("data:"):
        s = s.split(",", 1)[1]
    if not s:
        return None

    return {"base64": s}


# Backward-compatible alias for WhatsApp-local imports
_build_utterance_with_quoted_context = build_utterance_with_quoted_context


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
        convo_obj = await get_conversation_with_lock(sender, agent_id=agent_id)

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
    """Finalize a WhatsApp interaction after walker execution."""
    await finalize_interaction_from_webhook(walker, agent_id, sender)


async def _clear_whatsapp_typing(
    agent: Any, agent_id: str, sender: str, is_group: bool = False
) -> None:
    """Clear typing indicator for WhatsApp. Safe to call multiple times."""
    try:
        if not agent:
            return
        whatsapp_action = await agent.get_action_by_type("WhatsAppAction")
        if whatsapp_action and whatsapp_action.is_configured():
            wa = await whatsapp_action.api()
            await wa.set_typing_status(phone=sender, value=False, is_group=is_group)
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
    *,
    vision_base64: Optional[str] = None,
    vision_mime: Optional[str] = None,
) -> Dict[str, Any]:
    """Handle media batching logic with thread-safe batch manager.

    Uses the global ``_batch_manager``. Mode (in-memory vs persistent) is chosen
    inside ``get_or_create_batch`` via ``is_serverless_mode()``.

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
            vision_base64=vision_base64,
            vision_mime=vision_mime,
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
            wa = await whatsapp_action.api()
            typing_result = await wa.set_typing_status(
                phone=sender,
                value=True,
                is_group=data.isGroup,
                message_id=getattr(data, "message_id", "") or "",
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
            media_b64 = media_b64.strip()

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
                    pub = get_public_base_url().rstrip("/")
                    media_url = f"{pub}{media_url}" if pub else media_url
                    logger.debug(f"Saved media for user {sender}: {media_url}")

                    # visitor.data pattern: whatsapp_payload + top-level keys
                    data_dict = {
                        "whatsapp_payload": _convert_message_payload_to_dict(data)
                    }

                    # Inline base64 for LLM vision: OpenAI cannot fetch many public URLs
                    # (e.g. ngrok-free.app returns an HTML interstitial to their servers).
                    vision_b64_arg: Optional[str] = None
                    vision_mime_arg: Optional[str] = None
                    mt = (data.message_type or "").lower()
                    mime = (data.mime_type or "").lower()
                    if mt == "image" or mime.startswith("image/"):
                        vision_b64_arg = media_b64
                        vision_mime_arg = (data.mime_type or "").strip() or "image/jpeg"

                    # Handle batching for media messages
                    return await _handle_media_batching(
                        sender,
                        media_url,
                        utterance,
                        data_dict,
                        agent_id,
                        whatsapp_action,
                        vision_base64=vision_b64_arg,
                        vision_mime=vision_mime_arg,
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


def _prepare_voice_for_stt(data: Any) -> Optional[Tuple[str, str]]:
    """Prepare WhatsApp voice message for STT action.

    Extracts raw base64 and resolves audio MIME type. Encapsulates
    WhatsApp-specific format knowledge; STT action remains generic.

    Args:
        data: MessagePayload object with media and optional mime_type

    Returns:
        (audio_base64, audio_type) or None if no valid media
    """
    if not data.media or not data.media.strip():
        return None

    media = data.media.strip()
    if "," in media:
        media = media.split(",")[1]

    if not media:
        return None

    if data.mime_type and data.mime_type.startswith("audio/"):
        audio_type = data.mime_type.split(";")[0].strip()
    else:
        audio_type = "audio/ogg"

    return (media, audio_type)


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
                wa = await whatsapp_action.api()
                typing_result = await wa.set_typing_status(
                    phone=sender,
                    value=True,
                    is_group=data.isGroup,
                    message_id=getattr(data, "message_id", "") or "",
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
            prepared = _prepare_voice_for_stt(data)
            if not prepared:
                logger.debug(f"No media data in voice message from {sender}")
                return {"status": "ignored", "response": "no audio data"}

            audio_b64, audio_type = prepared
            transcript = await stt_action.invoke_base64(
                audio_base64=audio_b64, audio_type=audio_type
            )

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
    whatsapp_action = None
    try:
        # Ensure WhatsApp adapter is registered (lazy init for Lambda cold start)
        # Fetch once and reuse for adapter, tts check, and walker creation
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
        data_dict = {}
        # Convert MessagePayload to dict for InteractWalker
        data_dict["whatsapp_payload"] = _convert_message_payload_to_dict(data)
        is_group = is_group or data_dict["whatsapp_payload"].get("isGroup", False)

        # When user sends PTT and TTS is configured, respond with voice (reuse whatsapp_action)
        if (
            data_dict["whatsapp_payload"].get("message_type") == "ptt"
            and whatsapp_action
            and whatsapp_action.tts_action
        ):
            data_dict["respond_with_voice"] = True

        # Extract image from quoted message when user replies to an image
        quoted = data_dict["whatsapp_payload"].get("quoted_message") or {}
        quoted_image = _extract_quoted_image(quoted)
        if quoted_image:
            existing = data_dict.get("image_urls") or []
            data_dict["image_urls"] = existing + [quoted_image]

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

        # Finalize interaction using helper function
        await finalize_whatsapp_interaction(walker, agent_id, sender)

    except DatabaseError:
        raise
    except Exception as e:
        logger.error(
            f"Error processing WhatsApp interaction for agent {agent_id}: {e}",
            exc_info=True,
        )
    finally:
        await _clear_whatsapp_typing(agent, agent_id, sender, is_group)


async def is_directed_message(action_node: WhatsAppAction, data: Any) -> bool:
    """Determine if message is directed at the bot.

    Args:
        action_node: WhatsAppAction instance
        data: Message data dict

    Returns:
        True if message is directed at bot, False otherwise
    """

    if not data.isGroup:
        return True

    # Extract body from message or caption
    body = data.body or data.caption or ""
    matches = re.findall(r"@(\d+)", body)

    # Check mentionedIds if no matches in body
    if not matches and data.mentionedIds:
        matches = [mid.split("@")[0] for mid in data.mentionedIds]

    if not matches:
        return False

    receiver = data.receiver.split("@")[0]

    wa = await action_node.api()
    if action_node.provider == "wwebjs":
        tagged_phones = await asyncio.gather(
            *[wa.convert_lid_to_phone_number(tid) for tid in matches]
        )
    else:
        tagged_phones = matches

    for tagged in tagged_phones:
        if tagged == receiver:
            return True

    # Check group members if direct match failed
    group_id = data.sender
    result = await wa.group_members(group_id)
    if result and result.get("status") == "success":
        group_members = result.get("response", [])
        for item in group_members:
            user_id = item.get("id", {}).get("user")
            if user_id in matches and item.get("formattedName") == "You":
                return True

    return False


__all__ = [
    "ConversationLockManager",
    "MediaBatchManager",
    "create_whatsapp_walker",
    "finalize_whatsapp_interaction",
    "get_conversation_with_lock",
    "get_whatsapp_action",
    "normalize_result",
]
