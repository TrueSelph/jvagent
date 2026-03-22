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

BATCH MODES (from ``jvspatial.is_serverless_mode()`` only; uses ``get_current_server().config`` when set):
- async: In-memory batching with timer tasks via ``create_task`` (Shape B, long-running process).
- deferred: Persistent batching in the prime database via ``Database.find_one_and_update`` (same
  compound-op stack as ``claim_record`` / ``delete_claimed_record``), plus ``create_task`` (Shape A)
  for follow-up processing. Deferred dispatch is deduped per sender using ``media_batch_window``;
  the handler payload includes ``process_at`` so scheduled invokes do not double-wait.
  Strongest concurrency guarantees on MongoDB; other adapters use RMW paths.
  Actual transport (Lambda, SQS, EventBridge, noop, etc.) is chosen inside jvspatial.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from jvspatial import (
    claim_record,
    create_task,
    delete_claimed_record,
    is_serverless_mode,
    register_deferred_invoke_handler,
    release_claim,
)
from jvspatial.db import get_prime_database
from jvspatial.exceptions import DatabaseError

from jvagent.core.agent import Agent

logger = logging.getLogger(__name__)


MEDIA_BATCHES_COLLECTION = "media_batches"

BATCH_RECEIVED_RESPONSE: Dict[str, str] = {
    "status": "received",
    "response": "media batched",
}


def _get_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    """Get API payload from visitor.data (whatsapp_payload pattern or legacy flat)."""
    return data.get("whatsapp_payload") or data


def _is_vision_image(item: Dict[str, Any]) -> bool:
    """Return True if item is an image suitable for LLM vision input."""
    mt = item.get("message_type") or ""
    mime = item.get("mime_type") or ""
    return mt == "image" or (mime and mime.startswith("image/"))


def _media_item(
    media_url: str, utterance: Optional[str], data_dict: Dict[str, Any]
) -> Dict[str, Any]:
    """Single media entry for a batch (types from visitor payload)."""
    payload = _get_payload(data_dict)
    return {
        "url": media_url,
        "utterance": utterance,
        "message_type": payload.get("message_type"),
        "mime_type": payload.get("mime_type"),
    }


# Constants for batch management
BATCH_MAX_SIZE = 10  # Maximum number of media items per batch
BATCH_TTL_SECONDS = 300  # Time-to-live for abandoned batches (5 minutes)
BATCH_CLEANUP_INTERVAL = 60  # Run cleanup every 60 seconds


def _get_media_batch_mode() -> str:
    """Return ``async`` or ``deferred`` based solely on jvspatial serverless detection."""
    return "deferred" if is_serverless_mode() else "async"


class MediaBatchManager:
    """Thread-safe manager for media message batching.

    Handles concurrent access from multiple users by using per-user locks.
    Includes TTL-based cleanup to prevent memory leaks from abandoned batches.
    """

    def __init__(self):
        self._batches: Dict[str, Dict[str, Any]] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()  # For lock creation
        self._last_cleanup = time.time()

    async def _get_lock(self, sender: str) -> asyncio.Lock:
        """Get or create a lock for a specific sender (thread-safe)."""
        async with self._global_lock:
            if sender not in self._locks:
                self._locks[sender] = asyncio.Lock()
            return self._locks[sender]

    async def process_single_media_inline(
        self,
        sender: str,
        media_url: str,
        utterance: Optional[str],
        data_dict: Dict[str, Any],
        agent_id: str,
    ) -> None:
        """Process a single media item inline (no batching).

        Used when batching must not apply (e.g. single-item pass-through).
        """
        batch = {
            "media_items": [_media_item(media_url, utterance, data_dict)],
            "data": data_dict,
            "agent_id": agent_id,
        }
        await MediaBatchManager.execute_batch_from_record(sender, batch)

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
        mode = _get_media_batch_mode()
        if mode == "deferred":
            return await self._get_or_create_batch_persistent(
                sender, media_url, utterance, data_dict, agent_id, whatsapp_action
            )

        lock = await self._get_lock(sender)

        async with lock:
            current_time = time.time()

            if sender in self._batches:
                batch = self._batches[sender]

                # Check max batch size
                if len(batch["media_items"]) >= BATCH_MAX_SIZE:
                    logger.debug(
                        f"Media batch for user {sender} reached max size ({BATCH_MAX_SIZE}), "
                        f"processing immediately"
                    )
                    # Process current batch immediately
                    await MediaBatchManager.execute_batch_from_record(sender, batch)
                    # Create new batch for this media
                    batch = self._create_new_batch(
                        media_url,
                        utterance,
                        data_dict,
                        agent_id,
                        whatsapp_action,
                        current_time,
                    )
                    self._batches[sender] = batch
                else:
                    # Add to existing batch
                    batch["media_items"].append(
                        _media_item(media_url, utterance, data_dict)
                    )
                    batch["updated_at"] = current_time

                    await self._attach_batch_timer_or_run_now(
                        sender, batch, whatsapp_action.media_batch_window
                    )
                    logger.debug(
                        f"Added media to existing batch for user {sender}, "
                        f"batch size: {len(batch['media_items'])}, resetting timer"
                    )
            else:
                # Create new batch
                batch = self._create_new_batch(
                    media_url,
                    utterance,
                    data_dict,
                    agent_id,
                    whatsapp_action,
                    current_time,
                )
                self._batches[sender] = batch

                await self._attach_batch_timer_or_run_now(
                    sender, batch, whatsapp_action.media_batch_window
                )
                logger.debug(
                    f"Created new media batch for user {sender}, "
                    f"will process in {whatsapp_action.media_batch_window}s"
                )

            # Schedule cleanup if needed
            await self._maybe_schedule_cleanup()

            return dict(BATCH_RECEIVED_RESPONSE)

    async def _timer_or_flush_in_memory(self, sender: str, window: float) -> None:
        """Debounce with sleep until ``window`` elapses, then process in-memory batch.

        Only used in non-serverless (async) mode; deferred path never calls this.
        """
        await self._schedule_batch_processing(sender, window)

    async def _attach_batch_timer_or_run_now(
        self, sender: str, batch: Dict[str, Any], window: float
    ) -> None:
        """Reset debounce timer, or run batch immediately if tasks are unavailable."""
        tt = batch.get("timer_task")
        if tt and not tt.done():
            tt.cancel()

        task = await create_task(
            self._timer_or_flush_in_memory(sender, window),
            name=f"media_batch_timer_{sender}",
        )
        if task is not None:
            batch["timer_task"] = task

    async def _get_or_create_batch_persistent(
        self,
        sender: str,
        media_url: str,
        utterance: Optional[str],
        data_dict: Dict[str, Any],
        agent_id: str,
        whatsapp_action: Any,
    ) -> Dict[str, Any]:
        """Add media to persistent batch (serverless / prime database).

        Uses ``find_one_and_update`` with ``$push`` (native atomic on MongoDB; default adapter RMW).
        Dispatches a deferred task when invoke dedup allows; ``process_at`` defers
        claiming until the batch window has elapsed.
        """
        current_time = time.time()
        db = get_prime_database()
        media_item = _media_item(media_url, utterance, data_dict)

        update: Dict[str, Any] = {
            "$push": {"media_items": media_item},
            "$set": {"updated_at": current_time, "data": data_dict},
            "$setOnInsert": {"agent_id": agent_id, "created_at": current_time},
        }

        result = await db.find_one_and_update(
            MEDIA_BATCHES_COLLECTION,
            {"_id": sender},
            update,
            upsert=True,
        )

        if result is None:
            return {"status": "error", "response": "batch update failed"}

        batch_size = len(result.get("media_items", []))
        if batch_size >= BATCH_MAX_SIZE:
            logger.debug(
                f"Media batch for user {sender} reached max size ({BATCH_MAX_SIZE}), "
                f"processing immediately"
            )
            batch_claim, token = await claim_record(
                db, MEDIA_BATCHES_COLLECTION, sender
            )
            if batch_claim and token:
                await MediaBatchManager._finalize_claimed_persistent_batch(
                    db,
                    sender,
                    batch_claim,
                    token,
                    exc_log="Failed to process max-size batch for %s: %s",
                )
            return dict(BATCH_RECEIVED_RESPONSE)

        # Deferred dispatch with process_at so work runs after media_batch_window.
        # Single media also waits (no inline shortcut) so rapid multi-media coalesce.
        # Dedup: align with media_batch_window so a second invoke is not scheduled
        # while the first window is still open (avoids redundant Lambda/SQS sends).
        batch_window = float(whatsapp_action.media_batch_window)
        invoke_query: Dict[str, Any] = {
            "_id": sender,
            "$or": [
                {"invoked_at": {"$exists": False}},
                {"invoked_at": {"$lt": current_time - batch_window}},
            ],
        }
        invoke_winner = await db.find_one_and_update(
            MEDIA_BATCHES_COLLECTION,
            invoke_query,
            {"$set": {"invoked_at": current_time}},
        )
        if invoke_winner:
            process_at = current_time + batch_window
            await create_task(
                "jvagent.whatsapp.media_batch",
                {
                    "sender": sender,
                    "media_batch_window": batch_window,
                    "process_at": process_at,
                },
                run_at=process_at,
                name=f"media_batch_deferred_{sender}",
            )

        logger.debug(
            f"Added media to persistent batch for user {sender}, "
            f"batch size: {batch_size}"
        )
        return dict(BATCH_RECEIVED_RESPONSE)

    async def flush_pending_batch_if_stale(
        self, sender: str, media_batch_window: float
    ) -> None:
        """Process pending batch for sender if it has exceeded the batch window.

        Safety net when deferred dispatch does not run. Call at webhook entry
        before handling the current message. Serverless only.
        """
        if not is_serverless_mode():
            return
        current_time = time.time()
        db = get_prime_database()
        batch = await db.get(MEDIA_BATCHES_COLLECTION, sender)
        if not batch:
            return
        if current_time - batch.get("updated_at", 0) < media_batch_window:
            return
        batch_claim, token = await claim_record(db, MEDIA_BATCHES_COLLECTION, sender)
        if not batch_claim or not token:
            return
        logger.debug(f"Flushing stale batch for user {sender} (safety net)")
        await MediaBatchManager._finalize_claimed_persistent_batch(
            db,
            sender,
            batch_claim,
            token,
            exc_log="Stale batch flush failed for %s: %s",
        )

    @staticmethod
    async def _finalize_claimed_persistent_batch(
        db: Any,
        sender: str,
        batch_claim: Dict[str, Any],
        token: str,
        *,
        exc_log: str,
    ) -> bool:
        """Execute claimed batch, delete document, or release claim on failure.

        Returns True if processing succeeded (delete may still have failed, logged).
        Returns False if processing failed after releasing the claim.
        """
        try:
            await MediaBatchManager.execute_batch_from_record(sender, batch_claim)
        except Exception as exc:
            logger.error(exc_log, sender, exc)
            await release_claim(db, MEDIA_BATCHES_COLLECTION, sender, token)
            return False

        if not await delete_claimed_record(db, MEDIA_BATCHES_COLLECTION, sender, token):
            logger.warning("Could not delete claimed media batch for %s", sender)
        return True

    @staticmethod
    async def execute_batch_from_record(sender: str, batch: Dict[str, Any]) -> None:
        """Normalize in-memory or persisted batch dict (from DB or memory) and process."""
        normalized = {
            "media_items": batch["media_items"],
            "data": batch["data"],
            "agent_id": batch["agent_id"],
        }
        await MediaBatchManager._process_batch_internal(sender, normalized)

    def _create_new_batch(
        self,
        media_url: str,
        utterance: Optional[str],
        data_dict: Dict[str, Any],
        agent_id: str,
        whatsapp_action: Any,
        current_time: float,
    ) -> Dict[str, Any]:
        """Create a new batch structure with per-item metadata."""
        return {
            "media_items": [_media_item(media_url, utterance, data_dict)],
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
            pass
        except Exception as e:
            logger.error(
                f"Error in scheduled batch processing for {sender}: {e}", exc_info=True
            )
            await self._cleanup_batch(sender)

    async def process_batch(self, sender: str) -> None:
        """Process and remove batch for sender (thread-safe)."""
        lock = await self._get_lock(sender)

        async with lock:
            if sender not in self._batches:
                return

            batch = self._batches.pop(sender)

        await MediaBatchManager.execute_batch_from_record(sender, batch)

    @staticmethod
    def _batch_utterance_and_media_urls(
        media_items: List[Dict[str, Any]], payload: Dict[str, Any]
    ) -> Tuple[str, List[str], List[str]]:
        """Build combined utterance and URL lists for visitor.data."""
        from .endpoint_helpers import _build_utterance_with_quoted_context

        all_media = [item["url"] for item in media_items]
        whatsapp_image_urls = [
            item["url"] for item in media_items if _is_vision_image(item)
        ]

        utterances = [
            item.get("utterance") for item in media_items if item.get("utterance")
        ]
        combined_utterance = (
            " | ".join(utterances)
            if utterances
            else "Please receive and interpret the attached media."
        )
        quoted = payload.get("quoted_message") or {}
        combined_utterance = (
            _build_utterance_with_quoted_context(quoted, combined_utterance)
            or combined_utterance
        )
        return combined_utterance, all_media, whatsapp_image_urls

    @staticmethod
    async def _spawn_walker_for_media_batch(
        agent_id: str,
        combined_utterance: str,
        sender: str,
        data: Dict[str, Any],
    ) -> bool:
        """Create walker, spawn agent, finalize interaction. Returns False on hard stop."""
        from .endpoint_helpers import (
            create_whatsapp_walker,
            finalize_whatsapp_interaction,
        )

        walker = await create_whatsapp_walker(
            agent_id, combined_utterance, sender, data
        )
        if not walker:
            return False

        try:
            agent = await Agent.get(agent_id)
            if not agent:
                logger.error(f"Agent {agent_id} not found for media batch processing")
                return False

            # Ensure WhatsApp adapter is registered (critical for Lambda batch processor)
            whatsapp_action = await agent.get_action_by_type("WhatsAppAction")
            if whatsapp_action:
                await whatsapp_action.ensure_adapter_registered()

            await walker.spawn(agent)
        except DatabaseError as e:
            logger.error(f"Database error spawning walker for user {sender}: {e}")
            return False
        except Exception as e:
            logger.error(f"Error spawning walker for user {sender}: {e}")
            return False

        await finalize_whatsapp_interaction(walker, agent_id, sender)
        return True

    @staticmethod
    async def _process_batch_internal(sender: str, batch: Dict[str, Any]) -> None:
        """Internal batch processing logic (stateless)."""
        from .endpoint_helpers import _clear_whatsapp_typing

        agent_id = batch["agent_id"]
        data = batch["data"]
        payload = _get_payload(data)
        is_group = payload.get("isGroup", False)

        try:
            media_items: List[Dict[str, Any]] = batch["media_items"]
            combined_utterance, all_media, whatsapp_image_urls = (
                MediaBatchManager._batch_utterance_and_media_urls(media_items, payload)
            )

            # visitor.data pattern: whatsapp_payload + top-level image_urls, whatsapp_media
            data["whatsapp_media"] = all_media
            data["image_urls"] = whatsapp_image_urls

            logger.debug(
                f"Processing batched media for user {sender}: {len(all_media)} items",
                extra={
                    "user_id": sender,
                    "media_count": len(all_media),
                    "agent_id": agent_id,
                },
            )

            await MediaBatchManager._spawn_walker_for_media_batch(
                agent_id, combined_utterance, sender, data
            )

        except Exception as e:
            logger.error(
                f"Error processing batched media for user {sender}: {e}",
                exc_info=True,
            )
        finally:
            agent = await Agent.get(agent_id)
            await _clear_whatsapp_typing(agent, agent_id, sender, is_group)

    async def _cleanup_batch(self, sender: str) -> None:
        """Remove batch for sender without processing (cleanup on error)."""
        lock = await self._get_lock(sender)
        async with lock:
            if sender in self._batches:
                batch = self._batches.pop(sender)
                if batch.get("timer_task") and not batch["timer_task"].done():
                    batch["timer_task"].cancel()

    async def _maybe_schedule_cleanup(self) -> None:
        """Run inline cleanup of stale batches if enough time has passed."""
        current_time = time.time()
        if current_time - self._last_cleanup > BATCH_CLEANUP_INTERVAL:
            self._last_cleanup = current_time
            await self._cleanup_stale_batches_inline()

    async def _cleanup_stale_batches_inline(self) -> None:
        """Remove batches that have exceeded TTL (in-memory async mode)."""
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

        async with self._global_lock:
            stale_locks = [s for s in self._locks if s not in self._batches]
            for sender in stale_locks:
                del self._locks[sender]

        if stale_senders:
            logger.debug(f"Cleaned up {len(stale_senders)} stale media batches")


async def process_persistent_batch(
    sender: str,
    media_batch_window: float,
    process_at: Optional[float] = None,
) -> bool:
    """Claim and process a persisted media batch after the batch window.

    Invoked from the jvspatial deferred-invoke handler (LWA / Lambda payload) after
    ``create_task`` (Shape A). Sleeps until ``process_at`` (or ``media_batch_window``
    when ``process_at`` is unset), then claims the document via ``jvspatial.claim_record``
    (``find_one_and_update``) and deletes it after successful processing
    (``find_one_and_delete`` via ``delete_claimed_record``), so crashes can be retried after
    ``JVSPATIAL_WORK_CLAIM_STALE_SECONDS`` (default 600).

    Runs only when ``is_serverless_mode()`` is true. Requires a prime database that implements
    the same compound operations as work-claim helpers; MongoDB gives the strongest guarantees
    under concurrent writers.

    Returns True if a batch was claimed and processed, False otherwise.
    """
    if not is_serverless_mode():
        return False
    if process_at is not None:
        delay = max(0, process_at - time.time())
        if delay > 0:
            await asyncio.sleep(delay)
    else:
        await asyncio.sleep(media_batch_window)

    db = get_prime_database()

    batch_claim, token = await claim_record(db, MEDIA_BATCHES_COLLECTION, sender)
    if not batch_claim or not token:
        return False

    ok = await MediaBatchManager._finalize_claimed_persistent_batch(
        db,
        sender,
        batch_claim,
        token,
        exc_log="Failed to process batch for sender %s: %s",
    )
    return ok


async def handle_whatsapp_media_batch_deferred_event(
    event: Dict[str, Any],
) -> Dict[str, Any]:
    """Deferred-invoke handler for ``jvagent.whatsapp.media_batch`` (LWA / jvspatial router)."""
    sender = event.get("sender")
    if not sender or not isinstance(sender, str):
        logger.warning("Deferred whatsapp media batch missing sender: %s", event)
        raise HTTPException(status_code=400, detail="Missing sender")

    media_batch_window = float(event.get("media_batch_window", 1.5))
    process_at = event.get("process_at")
    if process_at is not None:
        process_at = float(process_at)

    logger.debug("Deferred whatsapp media batch for sender %s", sender)
    try:
        processed = await process_persistent_batch(
            sender, media_batch_window, process_at=process_at
        )
        if processed:
            logger.info("Processed media batch for sender %s (deferred invoke)", sender)
        return {"processed": processed}
    except Exception as e:
        logger.error(
            "Error processing batch for sender %s: %s", sender, e, exc_info=True
        )
        raise HTTPException(status_code=500, detail="Batch processing failed") from e


register_deferred_invoke_handler(
    "jvagent.whatsapp.media_batch",
    handle_whatsapp_media_batch_deferred_event,
)
