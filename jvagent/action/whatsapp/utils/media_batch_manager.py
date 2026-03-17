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

BATCH MODES (derived from BACKGROUND_PROCESSING + AWS_LAMBDA_FUNCTION_NAME):
- async: In-memory batching with background timer tasks. When BACKGROUND_PROCESSING=true.
- disabled: No batching; each media processed inline. When BACKGROUND_PROCESSING=false and not Lambda.
- lambda: Persistent batching via MongoDB + Lambda async invoke. When BACKGROUND_PROCESSING=false and
  AWS_LAMBDA_FUNCTION_NAME is set. Supports self-invoke; LWA routes direct-invoke payloads to
  POST /api/_internal/whatsapp/batch via AWS_LWA_PASS_THROUGH_PATH.
"""

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from jvspatial.async_utils import create_background_task
from jvspatial.config import use_background_processing
from jvspatial.db import get_prime_database
from jvspatial.exceptions import DatabaseError

from jvagent.core.agent import Agent

logger = logging.getLogger(__name__)


MEDIA_BATCHES_COLLECTION = "media_batches"


def _get_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    """Get API payload from visitor.data (whatsapp_payload pattern or legacy flat)."""
    return data.get("whatsapp_payload") or data


def _is_vision_image(item: Dict[str, Any]) -> bool:
    """Return True if item is an image suitable for LLM vision input."""
    mt = item.get("message_type") or ""
    mime = item.get("mime_type") or ""
    return mt == "image" or (mime and mime.startswith("image/"))


# Constants for batch management
BATCH_MAX_SIZE = 10  # Maximum number of media items per batch
BATCH_TTL_SECONDS = 300  # Time-to-live for abandoned batches (5 minutes)
BATCH_CLEANUP_INTERVAL = 60  # Run cleanup every 60 seconds
INVOKE_DEDUP_SECONDS = 1.0  # Min seconds between batch Lambda invokes per sender


def _get_media_batch_mode(whatsapp_action: Any = None) -> str:
    """Resolve media batch mode from BACKGROUND_PROCESSING and AWS_LAMBDA_FUNCTION_NAME.

    - BACKGROUND_PROCESSING=true -> async (in-memory batching with background tasks)
    - BACKGROUND_PROCESSING=false + Lambda -> lambda (persistent batching via MongoDB + Lambda)
    - BACKGROUND_PROCESSING=false + not Lambda -> disabled (inline, no batching)

    The whatsapp_action parameter is ignored; kept for backward compatibility with call sites.
    """
    if use_background_processing():
        return "async"
    return "lambda" if bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME")) else "disabled"


def _get_lambda_client():
    """Lazy singleton for boto3 Lambda client (avoids per-invoke creation overhead)."""
    if _lambda_client_cache[0] is None:
        import boto3

        _lambda_client_cache[0] = boto3.client("lambda")
    return _lambda_client_cache[0]


# Module-level cache for Lambda client; boto3 clients are thread-safe
_lambda_client_cache: List[Optional[Any]] = [None]
_scheduler_client_cache: List[Optional[Any]] = [None]


def _get_scheduler_client():
    """Lazy singleton for boto3 EventBridge Scheduler client."""
    if _scheduler_client_cache[0] is None:
        import boto3

        _scheduler_client_cache[0] = boto3.client("scheduler")
    return _scheduler_client_cache[0]


def _create_eventbridge_schedule(
    sender: str,
    media_batch_window: float,
    process_at: float,
) -> bool:
    """Create one-time EventBridge schedule to invoke batch Lambda at process_at.

    Avoids in-Lambda sleep (billed time). Returns True on success.
    Requires WHATSAPP_EVENTBRIDGE_SCHEDULER_ENABLED=true and WHATSAPP_EVENTBRIDGE_ROLE_ARN.
    """
    import re

    if os.environ.get("WHATSAPP_EVENTBRIDGE_SCHEDULER_ENABLED", "").lower() != "true":
        return False
    role_arn = os.environ.get("WHATSAPP_EVENTBRIDGE_ROLE_ARN", "").strip()
    lambda_arn = os.environ.get("WHATSAPP_EVENTBRIDGE_LAMBDA_ARN", "").strip()
    if not lambda_arn:
        func_name = os.environ.get(
            "WHATSAPP_MEDIA_BATCH_PROCESSOR_FUNCTION", ""
        ).strip()
        if func_name:
            region = os.environ.get("AWS_REGION", "us-east-1")
            account = os.environ.get("AWS_ACCOUNT_ID", "")
            if account:
                lambda_arn = f"arn:aws:lambda:{region}:{account}:function:{func_name}"
    if not role_arn or not lambda_arn:
        return False
    try:
        from datetime import datetime, timezone

        client = _get_scheduler_client()
        schedule_group = os.environ.get(
            "WHATSAPP_EVENTBRIDGE_SCHEDULER_GROUP", "default"
        ).strip()
        # Sanitize sender for schedule name (alphanumeric, hyphen, underscore)
        safe_sender = re.sub(r"[^a-zA-Z0-9_-]", "_", sender)[:32]
        name = f"wa-batch-{safe_sender}-{int(process_at * 1000)}"
        at_time = datetime.fromtimestamp(process_at, tz=timezone.utc)
        schedule_expr = f"at({at_time.strftime('%Y-%m-%dT%H:%M:%S')})"
        payload = json.dumps(
            {"sender": sender, "media_batch_window": media_batch_window}
        )
        client.create_schedule(
            Name=name,
            GroupName=schedule_group,
            ScheduleExpression=schedule_expr,
            ScheduleExpressionTimezone="UTC",
            FlexibleTimeWindow={"Mode": "OFF"},
            Target={
                "Arn": lambda_arn,
                "RoleArn": role_arn,
                "Input": payload,
            },
            ActionAfterCompletion="DELETE",
        )
        logger.info(
            f"Created EventBridge schedule for sender {sender} at {schedule_expr}"
        )
        return True
    except Exception as e:
        logger.warning(
            f"EventBridge schedule failed for {sender}, falling back to Lambda invoke: {e}"
        )
        return False


def _invoke_lambda_async(
    sender: str,
    media_batch_window: float,
    process_at: Optional[float] = None,
) -> None:
    """Asynchronously invoke the batch processing Lambda (fire-and-forget)."""
    func_name = os.environ.get("WHATSAPP_MEDIA_BATCH_PROCESSOR_FUNCTION", "").strip()
    if not func_name:
        logger.warning(
            "WHATSAPP_MEDIA_BATCH_PROCESSOR_FUNCTION not set, skipping async invoke"
        )
        return
    try:
        # Prefer EventBridge Scheduler when process_at set (avoids billed sleep time)
        if process_at is not None and _create_eventbridge_schedule(
            sender, media_batch_window, process_at
        ):
            return
        client = _get_lambda_client()
        payload_dict: Dict[str, Any] = {
            "sender": sender,
            "media_batch_window": media_batch_window,
        }
        if process_at is not None:
            payload_dict["process_at"] = process_at
        payload = json.dumps(payload_dict)
        client.invoke(
            FunctionName=func_name,
            InvocationType="Event",
            Payload=payload,
        )
        logger.info(f"Invoked batch processor Lambda for sender {sender}")
    except Exception as e:
        logger.error(f"Failed to invoke batch processor Lambda: {e}", exc_info=True)


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

    async def process_single_media_inline(
        self,
        sender: str,
        media_url: str,
        utterance: Optional[str],
        data_dict: Dict[str, Any],
        agent_id: str,
    ) -> None:
        """Process a single media item inline (no batching).

        Used for Lambda inline-only path and single-media pass-through.
        """
        payload = _get_payload(data_dict)
        media_item = {
            "url": media_url,
            "utterance": utterance,
            "message_type": payload.get("message_type"),
            "mime_type": payload.get("mime_type"),
        }
        batch = {
            "media_items": [media_item],
            "data": data_dict,
            "agent_id": agent_id,
        }
        await self._process_batch_internal(sender, batch)

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
        mode = _get_media_batch_mode(whatsapp_action)
        if mode == "lambda":
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
                    await self._process_batch_internal(sender, batch)
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
                    payload = _get_payload(data_dict)
                    batch["media_items"].append(
                        {
                            "url": media_url,
                            "utterance": utterance,
                            "message_type": payload.get("message_type"),
                            "mime_type": payload.get("mime_type"),
                        }
                    )
                    batch["updated_at"] = current_time

                    # Cancel existing timer and start a new one
                    if batch.get("timer_task") and not batch["timer_task"].done():
                        batch["timer_task"].cancel()

                    task = create_background_task(
                        self._schedule_batch_processing(
                            sender, whatsapp_action.media_batch_window
                        ),
                        name=f"media_batch_timer_{sender}",
                    )
                    if task is not None:
                        batch["timer_task"] = task
                    else:
                        await self._process_batch_internal(sender, batch)
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

                task = create_background_task(
                    self._schedule_batch_processing(
                        sender, whatsapp_action.media_batch_window
                    ),
                    name=f"media_batch_timer_{sender}",
                )
                if task is not None:
                    batch["timer_task"] = task
                else:
                    await self._process_batch_internal(sender, batch)
                logger.debug(
                    f"Created new media batch for user {sender}, "
                    f"will process in {whatsapp_action.media_batch_window}s"
                )

            # Schedule cleanup if needed
            await self._maybe_schedule_cleanup()

            return {"status": "received", "response": "media batched"}

    async def _get_or_create_batch_persistent(
        self,
        sender: str,
        media_url: str,
        utterance: Optional[str],
        data_dict: Dict[str, Any],
        agent_id: str,
        whatsapp_action: Any,
    ) -> Dict[str, Any]:
        """Add media to persistent batch (Lambda + MongoDB path).

        Uses atomic find_one_and_update with $push to avoid race conditions.
        Invokes batch Lambda only on new batch or when invoked_at is stale
        (invoke deduplication). Passes process_at so batch Lambda sleeps
        until the correct time before claiming.
        """
        current_time = time.time()
        db = get_prime_database()
        payload = _get_payload(data_dict)
        media_item = {
            "url": media_url,
            "utterance": utterance,
            "message_type": payload.get("message_type"),
            "mime_type": payload.get("mime_type"),
        }

        update: Dict[str, Any] = {
            "$push": {"media_items": media_item},
            "$set": {"updated_at": current_time, "data": data_dict},
            "$setOnInsert": {"agent_id": agent_id, "created_at": current_time},
        }

        try:
            result = await db.find_one_and_update(
                MEDIA_BATCHES_COLLECTION,
                {"_id": sender},
                update,
                upsert=True,
            )
        except NotImplementedError:
            # Fallback for non-MongoDB (e.g. JSON) - not used in persistent path
            result = await self._get_or_create_batch_persistent_fallback(
                sender, media_url, utterance, data_dict, agent_id, current_time
            )

        if result is None:
            return {"status": "error", "response": "batch update failed"}

        batch_size = len(result.get("media_items", []))
        if batch_size >= BATCH_MAX_SIZE:
            logger.debug(
                f"Media batch for user {sender} reached max size ({BATCH_MAX_SIZE}), "
                f"processing immediately"
            )
            deleted = await db.find_one_and_delete(
                MEDIA_BATCHES_COLLECTION, {"_id": sender}
            )
            if deleted:
                await self._process_batch_from_store(sender, deleted)
            return {"status": "received", "response": "media batched"}

        # Invoke batch Lambda with process_at so it waits media_batch_window before
        # claiming. Single media also waits (no inline shortcut) so rapid multi-media
        # can coalesce into one batch.
        # Invoke deduplication: atomically claim invoke slot; only winner invokes
        invoke_query: Dict[str, Any] = {
            "_id": sender,
            "$or": [
                {"invoked_at": {"$exists": False}},
                {"invoked_at": {"$lt": current_time - INVOKE_DEDUP_SECONDS}},
            ],
        }
        try:
            invoke_winner = await db.find_one_and_update(
                MEDIA_BATCHES_COLLECTION,
                invoke_query,
                {"$set": {"invoked_at": current_time}},
            )
        except NotImplementedError:
            invoke_winner = True  # Fallback: always invoke
        if invoke_winner:
            process_at = current_time + whatsapp_action.media_batch_window
            _invoke_lambda_async(
                sender,
                whatsapp_action.media_batch_window,
                process_at=process_at,
            )

        logger.debug(
            f"Added media to persistent batch for user {sender}, "
            f"batch size: {batch_size}"
        )
        return {"status": "received", "response": "media batched"}

    async def _get_or_create_batch_persistent_fallback(
        self,
        sender: str,
        media_url: str,
        utterance: Optional[str],
        data_dict: Dict[str, Any],
        agent_id: str,
        current_time: float,
    ) -> Optional[Dict[str, Any]]:
        """Fallback for DBs without find_one_and_update (read-modify-write)."""
        db = get_prime_database()
        payload = _get_payload(data_dict)
        existing = await db.get(MEDIA_BATCHES_COLLECTION, sender)
        if existing:
            existing["media_items"].append(
                {
                    "url": media_url,
                    "utterance": utterance,
                    "message_type": payload.get("message_type"),
                    "mime_type": payload.get("mime_type"),
                }
            )
            existing["updated_at"] = current_time
            existing["data"] = data_dict
            await db.save(MEDIA_BATCHES_COLLECTION, existing)
            return existing
        batch = {
            "_id": sender,
            "media_items": [
                {
                    "url": media_url,
                    "utterance": utterance,
                    "message_type": payload.get("message_type"),
                    "mime_type": payload.get("mime_type"),
                }
            ],
            "data": data_dict,
            "agent_id": agent_id,
            "created_at": current_time,
            "updated_at": current_time,
        }
        await db.save(MEDIA_BATCHES_COLLECTION, batch)
        return batch

    async def flush_pending_batch_if_stale(
        self, sender: str, media_batch_window: float, whatsapp_action: Any
    ) -> None:
        """Process pending batch for sender if it has exceeded the batch window.

        Safety net for when Lambda async invoke fails. Call at webhook entry
        before handling the current message. Only runs when mode is lambda.
        """
        if _get_media_batch_mode(whatsapp_action) != "lambda":
            return
        current_time = time.time()
        db = get_prime_database()
        batch = await db.get(MEDIA_BATCHES_COLLECTION, sender)
        if not batch:
            return
        if current_time - batch.get("updated_at", 0) < media_batch_window:
            return
        deleted = await db.find_one_and_delete(
            MEDIA_BATCHES_COLLECTION, {"_id": sender}
        )
        if deleted:
            logger.debug(f"Flushing stale batch for user {sender} (safety net)")
            await self._process_batch_from_store(sender, deleted)

    async def _process_batch_from_store(
        self, sender: str, batch: Dict[str, Any]
    ) -> None:
        """Process a batch loaded from persistent store (no action ref)."""
        batch_for_internal = {
            "media_items": batch["media_items"],
            "data": batch["data"],
            "agent_id": batch["agent_id"],
        }
        await self._process_batch_internal(sender, batch_for_internal)

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
        payload = _get_payload(data_dict)
        return {
            "media_items": [
                {
                    "url": media_url,
                    "utterance": utterance,
                    "message_type": payload.get("message_type"),
                    "mime_type": payload.get("mime_type"),
                }
            ],
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

        await self._process_batch_internal(sender, batch)

    async def _process_batch_internal(self, sender: str, batch: Dict[str, Any]) -> None:
        """Internal batch processing logic."""
        from .endpoint_helpers import (
            _build_utterance_with_quoted_context,
            _clear_whatsapp_typing,
            create_whatsapp_walker,
            finalize_whatsapp_interaction,
        )

        agent_id = batch["agent_id"]
        data = batch["data"]
        payload = _get_payload(data)
        is_group = payload.get("isGroup", False)

        try:
            media_items: List[Dict[str, Any]] = batch["media_items"]
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

            walker = await create_whatsapp_walker(
                agent_id, combined_utterance, sender, data
            )
            if not walker:
                return

            try:
                agent = await Agent.get(agent_id)
                if not agent:
                    logger.error(
                        f"Agent {agent_id} not found for media batch processing"
                    )
                    return

                # Ensure WhatsApp adapter is registered (critical for Lambda batch processor)
                whatsapp_action = await agent.get_action_by_type("WhatsAppAction")
                if whatsapp_action:
                    await whatsapp_action.ensure_adapter_registered()

                await walker.spawn(agent)
            except DatabaseError as e:
                logger.error(f"Database error spawning walker for user {sender}: {e}")
                return
            except Exception as e:
                logger.error(f"Error spawning walker for user {sender}: {e}")
                return

            await finalize_whatsapp_interaction(walker, agent_id, sender)

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
    """Process a batch from persistent store if one exists.

    Called by the batch handler Lambda. Sleeps until process_at (or
    media_batch_window if process_at not provided) before claiming the batch.
    Only runs when in lambda mode (BACKGROUND_PROCESSING=false + AWS_LAMBDA_FUNCTION_NAME)
    and JVSPATIAL_DB_TYPE=mongodb.

    Returns True if a batch was found and processed, False otherwise.
    """
    if _get_media_batch_mode() != "lambda":
        return False
    if os.environ.get("JVSPATIAL_DB_TYPE") != "mongodb":
        return False
    if process_at is not None:
        delay = max(0, process_at - time.time())
        if delay > 0:
            await asyncio.sleep(delay)
    else:
        await asyncio.sleep(media_batch_window)
    db = get_prime_database()
    batch = await db.find_one_and_delete(MEDIA_BATCHES_COLLECTION, {"_id": sender})
    if not batch:
        return False
    manager = MediaBatchManager()
    await manager._process_batch_from_store(sender, batch)
    return True
