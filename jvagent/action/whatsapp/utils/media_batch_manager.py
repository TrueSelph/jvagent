"""Media batch manager for WhatsApp action.

This module provides thread-safe/async-safe media batching for multiple users
accessing the WhatsApp webhook concurrently.

CONFIGURATION RATIONALE:
- BATCH_MAX_SIZE (10): Maximum media items per batch before forcing processing.
  Prevents memory buildup from users sending many media files. WhatsApp typically
  allows 10 files to be sent together, aligning with this limit.

- BATCH_TTL_SECONDS (300): Abandoned batch cleanup threshold (5 minutes).
  If a batch hasn't been updated in 5 minutes, it's considered abandoned
  (e.g., user disconnected mid-upload) and is cleaned up to free memory.

- BATCH_CLEANUP_INTERVAL (60): How often to check for stale batches (1 minute).
  Balances cleanup frequency with CPU overhead. More frequent checks mean
  faster memory recovery but slightly higher CPU usage.

ERROR RECOVERY:
- On batch processing error, the batch is cleaned up to prevent retries
- Timer tasks are cancelled on batch removal to prevent orphaned tasks
- Per-user locks prevent race conditions during batch operations

LAMBDA COMPATIBILITY:
- Cleanup runs inline (awaited) during get_or_create_batch rather than via background task.
  This ensures cleanup completes within the same request and doesn't depend on
  background tasks that may be frozen when Lambda returns.
- Batch timer tasks still use create_background_task but are best-effort in Lambda;
  batches will be processed when next accessed if timer doesn't fire.
"""

import asyncio
import logging
import time
from typing import Any, Dict, Optional

from jvagent.core.agent import Agent
from jvspatial.exceptions import DatabaseError

from .task_helpers import create_background_task

logger = logging.getLogger(__name__)

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
                    logger.debug(
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
                    logger.debug(
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
                logger.debug(
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
        # Import here to avoid circular dependency
        from .endpoint_helpers import (
            _store_whatsapp_metadata_in_interaction,
            create_whatsapp_walker,
            finalize_whatsapp_interaction,
        )
        
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
            
            logger.debug(
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
            
            # Store WhatsApp-specific metadata in interaction for adapter retrieval
            await _store_whatsapp_metadata_in_interaction(walker, data)
            
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
        """Run inline cleanup of stale batches if enough time has passed.
        
        Lambda-compatible: Runs cleanup inline rather than via background task.
        """
        current_time = time.time()
        if current_time - self._last_cleanup > BATCH_CLEANUP_INTERVAL:
            self._last_cleanup = current_time
            # Run cleanup inline rather than in background task (Lambda-compatible)
            await self._cleanup_stale_batches_inline()
    
    async def _cleanup_stale_batches_inline(self) -> None:
        """Remove batches that have exceeded TTL (inline, Lambda-compatible)."""
        current_time = time.time()
        stale_senders = []
        
        async with self._global_lock:
            for sender, batch in self._batches.items():
                if current_time - batch.get("updated_at", 0) > BATCH_TTL_SECONDS:
                    stale_senders.append(sender)
        
        for sender in stale_senders:
            logger.debug(
                f"Cleaning up stale media batch for user {sender} (exceeded TTL)"
            )
            await self._cleanup_batch(sender)
        
        # Also clean up locks for senders with no active batches
        async with self._global_lock:
            stale_locks = [s for s in self._locks if s not in self._batches]
            for sender in stale_locks:
                del self._locks[sender]
        
        if stale_senders:
            logger.debug(f"Cleaned up {len(stale_senders)} stale media batches")
