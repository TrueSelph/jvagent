"""WhatsApp Action Endpoints."""

import asyncio
import logging
import base64
import time
from typing import Any, Dict, List, Optional

from fastapi import Request, HTTPException

from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.core.agent import Agent
from jvagent.core.app import App
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


# ============================================================================
# BACKGROUND TASK UTILITIES
# ============================================================================
# Helper functions for managing background tasks with proper exception handling.

def _handle_task_exception(task: asyncio.Task, name: str) -> None:
    """Handle exceptions from background tasks.
    
    Args:
        task: The completed task
        name: Name of the task for logging
    """
    try:
        task.result()
    except asyncio.CancelledError:
        # Task was cancelled, this is expected behavior
        pass
    except Exception as e:
        logger.error(f"Background task '{name}' failed: {e}", exc_info=True)


def create_background_task(coro, name: str = "background") -> asyncio.Task:
    """Create a background task with automatic exception logging.
    
    This wrapper ensures that any exceptions in fire-and-forget tasks
    are logged rather than silently swallowed.
    
    Args:
        coro: Coroutine to run as a background task
        name: Descriptive name for the task (used in error logs)
        
    Returns:
        The created asyncio.Task
    """
    task = asyncio.create_task(coro)
    task.add_done_callback(lambda t: _handle_task_exception(t, name))
    return task


# ============================================================================
# WHATSAPP ACTION HELPER
# ============================================================================

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


def normalize_result(result: Dict[str, Any], default_status: str = "sent") -> Dict[str, Any]:
    """Normalize API result by adding status field if missing.
    
    Args:
        result: API response dictionary
        default_status: Default status to use on success
        
    Returns:
        Result dict with normalized status
    """
    if isinstance(result, dict) and "status" not in result:
        result["status"] = default_status if result.get("success") or result.get("ok") else "failed"
    return result


async def create_whatsapp_walker(
    agent_id: str,
    utterance: str,
    sender: str,
    data_dict: Dict[str, Any],
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
                stream=False,  # WhatsApp uses non-streaming mode
            )
        else:
            return InteractWalker(
                agent_id=agent_id,
                utterance=utterance,
                channel="whatsapp",
                data=data_dict,
                user_id=sender,
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


# ============================================================================
# CONCURRENT-SAFE MEDIA BATCH MANAGER
# ============================================================================
# This class provides thread-safe/async-safe media batching for multiple users
# accessing the WhatsApp webhook concurrently.
#
# CONFIGURATION RATIONALE:
# - BATCH_MAX_SIZE (10): Maximum media items per batch before forcing processing.
#   Prevents memory buildup from users sending many media files. WhatsApp typically
#   allows 10 files to be sent together, aligning with this limit.
#
# - BATCH_TTL_SECONDS (300): Abandoned batch cleanup threshold (5 minutes).
#   If a batch hasn't been updated in 5 minutes, it's considered abandoned
#   (e.g., user disconnected mid-upload) and is cleaned up to free memory.
#
# - BATCH_CLEANUP_INTERVAL (60): How often to check for stale batches (1 minute).
#   Balances cleanup frequency with CPU overhead. More frequent checks mean
#   faster memory recovery but slightly higher CPU usage.
#
# ERROR RECOVERY:
# - On batch processing error, the batch is cleaned up to prevent retries
# - Timer tasks are cancelled on batch removal to prevent orphaned tasks
# - Per-user locks prevent race conditions during batch operations

# Constants for batch management
BATCH_MAX_SIZE = 10  # Maximum number of media items per batch
BATCH_TTL_SECONDS = 300  # Time-to-live for abandoned batches (5 minutes)
BATCH_CLEANUP_INTERVAL = 60  # Run cleanup every 60 seconds


class MediaBatchManager:
    """Thread-safe manager for media message batching.
    
    Handles concurrent access from multiple users by using per-user locks.
    Includes TTL-based cleanup to prevent memory leaks from abandoned batches.
    """
    
    def __init__(self):
        self._batches: Dict[str, Dict[str, Any]] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()  # For lock creation
        self._cleanup_task: Optional[asyncio.Task] = None
        self._last_cleanup = time.time()
    
    async def _get_lock(self, sender: str) -> asyncio.Lock:
        """Get or create a lock for a specific sender (thread-safe)."""
        async with self._global_lock:
            if sender not in self._locks:
                self._locks[sender] = asyncio.Lock()
            return self._locks[sender]
    
    async def get_or_create_batch(
        self,
        sender: str,
        media_url: str,
        utterance: Optional[str],
        data_dict: Dict[str, Any],
        agent_id: str,
        whatsapp_action: Any,
    ) -> Dict[str, Any]:
        """Add media to batch for sender (thread-safe).
        
        Returns:
            Dict with status and any relevant info
        """
        lock = await self._get_lock(sender)
        
        async with lock:
            current_time = time.time()
            
            if sender in self._batches:
                batch = self._batches[sender]
                
                # Check max batch size
                if len(batch["media_urls"]) >= BATCH_MAX_SIZE:
                    logger.warning(
                        f"Media batch for user {sender} reached max size ({BATCH_MAX_SIZE}), "
                        f"processing immediately"
                    )
                    # Process current batch immediately
                    await self._process_batch_internal(sender, batch)
                    # Create new batch for this media
                    batch = self._create_new_batch(
                        media_url, utterance, data_dict, agent_id, whatsapp_action, current_time
                    )
                    self._batches[sender] = batch
                else:
                    # Add to existing batch
                    batch["media_urls"].append(media_url)
                    batch["utterances"].append(utterance)
                    batch["updated_at"] = current_time
                    
                    # Cancel existing timer and start a new one
                    if batch.get("timer_task") and not batch["timer_task"].done():
                        batch["timer_task"].cancel()
                    
                    batch["timer_task"] = create_background_task(
                        self._schedule_batch_processing(sender, whatsapp_action.media_batch_window),
                        name=f"media_batch_timer_{sender}"
                    )
                    logger.info(
                        f"Added media to existing batch for user {sender}, "
                        f"batch size: {len(batch['media_urls'])}, resetting timer"
                    )
            else:
                # Create new batch
                batch = self._create_new_batch(
                    media_url, utterance, data_dict, agent_id, whatsapp_action, current_time
                )
                self._batches[sender] = batch
                
                # Start timer for batch processing
                batch["timer_task"] = create_background_task(
                    self._schedule_batch_processing(sender, whatsapp_action.media_batch_window),
                    name=f"media_batch_timer_{sender}"
                )
                logger.info(
                    f"Created new media batch for user {sender}, "
                    f"will process in {whatsapp_action.media_batch_window}s"
                )
            
            # Schedule cleanup if needed
            await self._maybe_schedule_cleanup()
            
            return {"status": "received", "response": "media batched"}
    
    def _create_new_batch(
        self,
        media_url: str,
        utterance: Optional[str],
        data_dict: Dict[str, Any],
        agent_id: str,
        whatsapp_action: Any,
        current_time: float,
    ) -> Dict[str, Any]:
        """Create a new batch structure."""
        return {
            "media_urls": [media_url],
            "utterances": [utterance],
            "data": data_dict,
            "agent_id": agent_id,
            "action": whatsapp_action,
            "timer_task": None,
            "created_at": current_time,
            "updated_at": current_time,
        }
    
    async def _schedule_batch_processing(self, sender: str, delay: float) -> None:
        """Schedule batch processing after a delay."""
        try:
            await asyncio.sleep(delay)
            await self.process_batch(sender)
        except asyncio.CancelledError:
            # Timer was cancelled, batch will be processed by new timer
            pass
        except Exception as e:
            logger.error(f"Error in scheduled batch processing for {sender}: {e}", exc_info=True)
            # Ensure cleanup happens even on error
            await self._cleanup_batch(sender)
    
    async def process_batch(self, sender: str) -> None:
        """Process and remove batch for sender (thread-safe)."""
        lock = await self._get_lock(sender)
        
        async with lock:
            if sender not in self._batches:
                return
            
            batch = self._batches.pop(sender)
            
        # Process outside the lock to avoid blocking other operations
        await self._process_batch_internal(sender, batch)
    
    async def _process_batch_internal(self, sender: str, batch: Dict[str, Any]) -> None:
        """Internal batch processing logic."""
        agent_id = batch["agent_id"]
        
        try:
            # Combine all media URLs
            all_media = batch["media_urls"]
            
            # Combine utterances or use default
            utterances = [u for u in batch["utterances"] if u]
            combined_utterance = " | ".join(utterances) if utterances else "I've attached media"
            
            # Use the data from the first message and add all media
            data = batch["data"]
            data["whatsapp_media"] = all_media
            
            logger.info(
                f"Processing batched media for user {sender}: {len(all_media)} items",
                extra={
                    "user_id": sender,
                    "media_count": len(all_media),
                    "agent_id": agent_id,
                }
            )
            
            # Create walker using helper function
            walker = await create_whatsapp_walker(agent_id, combined_utterance, sender, data)
            if not walker:
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
            
            # Finalize interaction using helper function
            await finalize_whatsapp_interaction(walker, agent_id, sender)
        
        except Exception as e:
            logger.error(
                f"Error processing batched media for user {sender}: {e}",
                exc_info=True,
            )
    
    async def _cleanup_batch(self, sender: str) -> None:
        """Remove batch for sender without processing (cleanup on error)."""
        lock = await self._get_lock(sender)
        async with lock:
            if sender in self._batches:
                batch = self._batches.pop(sender)
                if batch.get("timer_task") and not batch["timer_task"].done():
                    batch["timer_task"].cancel()
    
    async def _maybe_schedule_cleanup(self) -> None:
        """Schedule cleanup task if not already running."""
        current_time = time.time()
        if current_time - self._last_cleanup > BATCH_CLEANUP_INTERVAL:
            self._last_cleanup = current_time
            create_background_task(self._cleanup_stale_batches(), name="batch_cleanup")
    
    async def _cleanup_stale_batches(self) -> None:
        """Remove batches that have exceeded TTL (prevents memory leaks)."""
        current_time = time.time()
        stale_senders = []
        
        async with self._global_lock:
            for sender, batch in self._batches.items():
                if current_time - batch.get("updated_at", 0) > BATCH_TTL_SECONDS:
                    stale_senders.append(sender)
        
        for sender in stale_senders:
            logger.warning(
                f"Cleaning up stale media batch for user {sender} (exceeded TTL)"
            )
            await self._cleanup_batch(sender)
        
        # Also clean up locks for senders with no active batches
        async with self._global_lock:
            stale_locks = [s for s in self._locks if s not in self._batches]
            for sender in stale_locks:
                del self._locks[sender]


# Global instance of the batch manager
_batch_manager = MediaBatchManager()


# ============================================================================
# CONCURRENT-SAFE CONVERSATION LOCK MANAGER
# ============================================================================
# Prevents duplicate conversation creation when multiple messages from the
# same user arrive simultaneously.
#
# CONFIGURATION RATIONALE:
# - CONVERSATION_LOCK_TTL_SECONDS (30): Lock expiry threshold.
#   Locks are lightweight and short-lived - 30 seconds is enough for conversation
#   lookup/creation while preventing indefinite locks from abandoned operations.
#
# - CONVERSATION_LOCK_CLEANUP_INTERVAL (120): How often to clean stale locks (2 min).
#   Less frequent than batch cleanup because locks are smaller and less memory-
#   intensive. 2 minutes provides good balance between memory recovery and overhead.
#
# ERROR RECOVERY:
# - Locks are only removed if not currently held (prevents removing active locks)
# - Lock acquisition is always gated by the global lock to prevent race conditions
# - If a lock is held during cleanup, it's skipped until the next cleanup cycle

CONVERSATION_LOCK_TTL_SECONDS = 30  # Lock expires after 30 seconds
CONVERSATION_LOCK_CLEANUP_INTERVAL = 120  # Run cleanup every 2 minutes


class ConversationLockManager:
    """Thread-safe manager for conversation access locks.
    
    Prevents race conditions when multiple messages from the same user
    trigger concurrent conversation lookups that could create duplicates.
    """
    
    def __init__(self):
        self._locks: Dict[str, asyncio.Lock] = {}
        self._lock_timestamps: Dict[str, float] = {}
        self._global_lock = asyncio.Lock()
        self._last_cleanup = time.time()
    
    async def acquire_lock(self, user_id: str) -> asyncio.Lock:
        """Get or create a lock for a specific user (thread-safe).
        
        Returns the lock - caller must use it with `async with` pattern.
        """
        async with self._global_lock:
            if user_id not in self._locks:
                self._locks[user_id] = asyncio.Lock()
            self._lock_timestamps[user_id] = time.time()
            
            # Schedule cleanup if needed
            current_time = time.time()
            if current_time - self._last_cleanup > CONVERSATION_LOCK_CLEANUP_INTERVAL:
                self._last_cleanup = current_time
                create_background_task(self._cleanup_stale_locks(), name="conversation_lock_cleanup")
            
            return self._locks[user_id]
    
    async def _cleanup_stale_locks(self) -> None:
        """Remove locks that haven't been used recently."""
        current_time = time.time()
        stale_users = []
        
        async with self._global_lock:
            for user_id, timestamp in list(self._lock_timestamps.items()):
                if current_time - timestamp > CONVERSATION_LOCK_TTL_SECONDS:
                    # Only remove if lock is not currently held
                    if user_id in self._locks and not self._locks[user_id].locked():
                        stale_users.append(user_id)
            
            for user_id in stale_users:
                del self._locks[user_id]
                del self._lock_timestamps[user_id]


# Global instance of the conversation lock manager
_conversation_lock_manager = ConversationLockManager()


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
                    return await _handle_media_batching(
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


async def _handle_media_batching(
    sender: str, media_url: str, utterance: Optional[str], 
    data_dict: Dict[str, Any], agent_id: str, whatsapp_action
) -> Dict[str, Any]:
    """Handle media batching logic with thread-safe batch manager.
    
    Uses the global _batch_manager for concurrent-safe operations.
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
        logger.error(f"Error handling media batching for user {sender}: {e}", exc_info=True)
        return {"status": "error", "response": "batching failed"}


async def _process_interaction_async(
    data, utterance: str, sender: str, agent_id: str, agent
) -> None:
    """Process the interaction in the background with improved error handling.
    
    Uses conversation locking to prevent race conditions when multiple
    messages from the same user arrive simultaneously.
    """
    try:
        # Convert MessagePayload to dict for InteractWalker
        data_dict = _convert_message_payload_to_dict(data)

        # Create walker using helper function
        walker = await create_whatsapp_walker(agent_id, utterance, sender, data_dict)
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

        # Validate sender - ignore status@broadcast messages completely
        if not sender or "status@broadcast" in sender or "status@broadcast" in data.receiver or sender == data.receiver:
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
        create_background_task(
            _process_interaction_async(data, utterance, sender, agent_id, agent),
            name=f"whatsapp_interaction_{sender}"
        )

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
    whatsapp_action = await get_whatsapp_action(action_id)

    if outbox:
        logger.warning("Outbox not implemented yet")
        return {"status": "outbox not implemented yet"}

    result = await whatsapp_action.api().send_message(
        phone=to, message=message, is_group=is_group, 
        is_newsletter=is_newsletter, message_id=message_id, options=options
    )
    return normalize_result(result, "sent")


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
    whatsapp_action = await get_whatsapp_action(action_id)
    result = await whatsapp_action.api().send_image(
        phone=to, 
        file_url=image_url, 
        caption=caption,
        filename=filename,
        is_group=is_group
    )
    return normalize_result(result, "sent")


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
    whatsapp_action = await get_whatsapp_action(action_id)
    result = await whatsapp_action.api().send_file(
        phone=to, 
        file_url=file_url, 
        caption=caption,
        filename=filename,
        is_group=is_group
    )
    return normalize_result(result, "sent")


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
    whatsapp_action = await get_whatsapp_action(action_id)
    result = await whatsapp_action.api().send_voice(
        phone=to, 
        file_url=voice_url, 
        is_group=is_group,
        quoted_message_id=quoted_message_id
    )
    return normalize_result(result, "sent")


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
    whatsapp_action = await get_whatsapp_action(action_id)
    result = await whatsapp_action.api().send_location(
        phone=to, 
        latitude=latitude,
        longitude=longitude,
        title=title,
        is_group=is_group
    )
    return normalize_result(result, "sent")


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
    whatsapp_action = await get_whatsapp_action(action_id)
    result = await whatsapp_action.api().create_group(name=name, participants=participants)
    return normalize_result(result, "created")


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
    whatsapp_action = await get_whatsapp_action(action_id)
    result = await whatsapp_action.api().add_group_participant(group_id=group_id, phone=phone)
    return normalize_result(result, "added")


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
    whatsapp_action = await get_whatsapp_action(action_id)
    result = await whatsapp_action.api().remove_group_participant(group_id=group_id, phone=phone)
    return normalize_result(result, "removed")


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
    whatsapp_action = await get_whatsapp_action(action_id)
    return await whatsapp_action.api().get_profile_picture(phone=phone)


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
    whatsapp_action = await get_whatsapp_action(action_id)
    return await whatsapp_action.api().status()


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
    whatsapp_action = await get_whatsapp_action(action_id)
    return await whatsapp_action.api().qrcode()


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
    whatsapp_action = await get_whatsapp_action(action_id)
    return await whatsapp_action.api().get_host_device()


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
    whatsapp_action = await get_whatsapp_action(action_id)
    result = await whatsapp_action.api().logout()
    return normalize_result(result, "logout")


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
    whatsapp_action = await get_whatsapp_action(action_id)
    result = await whatsapp_action.api().close_session()
    return normalize_result(result, "close")
