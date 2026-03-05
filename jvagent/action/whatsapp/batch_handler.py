"""Lambda entry point for WhatsApp media batch processing.

This module is invoked by the batch processor Lambda. It receives events
from async Lambda invokes, sleeps for the batch window, then atomically
claims and processes the batch from MongoDB.

Handler: jvagent.action.whatsapp.batch_handler.handler
"""

import asyncio
import json
import logging
from typing import Any, Dict

from .utils.media_batch_manager import process_persistent_batch

logger = logging.getLogger(__name__)


def handler(event: Dict[str, Any], context: Any) -> None:
    """Lambda handler for batch processing.

    Args:
        event: {"sender": str, "media_batch_window": float, "process_at": float (optional)}
        context: Lambda context (unused)
    """
    if isinstance(event, str):
        try:
            event = json.loads(event)
        except json.JSONDecodeError:
            logger.warning("Batch handler received invalid JSON event: %s", event[:200])
            return
    sender = event.get("sender") if isinstance(event, dict) else None
    if not sender:
        logger.warning("Batch handler received event without sender: %s", event)
        return

    media_batch_window = float(event.get("media_batch_window", 2.5))
    process_at = event.get("process_at")
    if process_at is not None:
        process_at = float(process_at)

    try:
        processed = asyncio.run(
            process_persistent_batch(sender, media_batch_window, process_at=process_at)
        )
        if processed:
            logger.info("Processed media batch for sender %s", sender)
        else:
            logger.debug(
                "No batch to process for sender %s (already processed)", sender
            )
    except Exception as e:
        logger.error(
            "Error processing batch for sender %s: %s", sender, e, exc_info=True
        )
        raise
