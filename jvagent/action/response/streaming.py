"""SSE streaming utilities for response bus."""

import asyncio
import json
import logging
from typing import Any, AsyncGenerator, Dict, Optional

from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)


def _sse_dedup_key(message: Any) -> tuple:
    """Dedup key for SSE replay overlap — (id, message_type, sequence)."""
    mid = getattr(message, "id", None) or getattr(message, "message_id", None) or ""
    mtype = getattr(message, "message_type", "") or ""
    meta = getattr(message, "metadata", None) or {}
    seq = meta.get("sequence") if isinstance(meta, dict) else None
    if seq is None:
        seq = getattr(message, "content", "") or ""
    return (mid, mtype, seq)


def format_sse_chunk(data: Dict[str, Any]) -> str:
    """Format data as SSE chunk.

    Args:
        data: Data dictionary to format

    Returns:
        SSE-formatted string (data: {json}\n\n)
    """
    json_data = json.dumps(data)
    return f"data: {json_data}\n\n"


async def stream_messages(
    session_id: str,
    response_bus: Any,
    interaction_id: Optional[str] = None,
    keepalive_seconds: Optional[float] = None,
    max_replay: Optional[int] = None,
) -> AsyncGenerator[str, None]:
    """Stream messages from response bus for a session.

    This generator yields SSE-formatted chunks as messages are published
    to the response bus for the given session.

    Args:
        session_id: Session identifier
        response_bus: ResponseBus instance
        interaction_id: Optional interaction ID to filter messages
        keepalive_seconds: When set, emit an SSE comment every this many idle
            seconds and use it as the queue poll interval. Long-lived
            subscriptions need this: proxies (nginx ``proxy_read_timeout``, ALB,
            Cloudflare) drop connections that stay silent for 60-100s. It also
            avoids the default 10-wakeups-per-second poll on an idle stream,
            which is fine for a turn-scoped stream but wasteful per parked tab.
        max_replay: When set, replay only the most recent N backlog messages.
            The backlog is never drained by streaming subscribers, so an
            unbounded replay re-sends the whole queue on every reconnect.

    Yields:
        SSE-formatted string chunks
    """
    # Idle poll interval. Turn-scoped streams keep the original tight loop so
    # end-of-walk detection stays snappy; long-lived ones poll at the keepalive.
    poll_timeout = keepalive_seconds if keepalive_seconds else 0.1
    # Subscribe to new messages using asyncio.Queue for real-time delivery
    message_queue: asyncio.Queue = asyncio.Queue()
    done = asyncio.Event()

    async def message_callback(message: Any) -> None:
        """Callback to receive new messages."""
        if interaction_id and message.interaction_id != interaction_id:
            return
        try:
            await message_queue.put(message)
        except Exception as e:
            logger.error(f"Error queuing message: {e}", exc_info=True)

    # Subscribe BEFORE replaying the backlog so no message published in between
    # is missed. This means a message can arrive on BOTH the backlog replay and
    # the live queue, so dedup by message_id: any queued message already sent
    # from the backlog is skipped. Previously the same message was delivered
    # twice. AUDIT-actions (M19).
    await response_bus.subscribe(session_id, message_callback, receive_chunks=True)

    try:
        # Send any existing messages first, recording their ids for dedup.
        replayed_ids: set = set()
        existing_messages = await response_bus.get_messages(session_id)
        if max_replay is not None and len(existing_messages) > max_replay:
            existing_messages = existing_messages[-max_replay:]
        for message in existing_messages:
            if interaction_id and message.interaction_id != interaction_id:
                continue
            replayed_ids.add(_sse_dedup_key(message))
            yield format_sse_chunk(message.to_dict())

        # Stream new messages as they arrive using queue-based waiting
        while True:
            try:
                # Wait for message with timeout to allow checking for done event
                message = await asyncio.wait_for(
                    message_queue.get(), timeout=poll_timeout
                )
                dedup_key = _sse_dedup_key(message)
                if dedup_key in replayed_ids:
                    # Already delivered from the backlog replay — drop the dup.
                    replayed_ids.discard(dedup_key)
                    continue
                yield format_sse_chunk(message.to_dict())
            except asyncio.TimeoutError:
                # Check if we should continue (allows graceful shutdown)
                if done.is_set():
                    break
                if keepalive_seconds:
                    # SSE comment — ignored by clients, keeps proxies from
                    # dropping an idle long-lived connection.
                    yield ": keepalive\n\n"
                continue
            except Exception as e:
                logger.error(f"Error streaming message: {e}", exc_info=True)
                break
    finally:
        # Signal done and cleanup subscription
        done.set()
        await response_bus.unsubscribe(session_id, message_callback)


def create_sse_response(
    generator: AsyncGenerator[str, None],
    headers: Optional[Dict[str, str]] = None,
) -> StreamingResponse:
    """Create SSE StreamingResponse.

    Args:
        generator: Async generator yielding SSE-formatted strings
        headers: Optional additional headers

    Returns:
        FastAPI StreamingResponse configured for SSE

    Note:
        Headers are configured for compatibility with:
        - AWS API Gateway (Lambda deployments)
        - Nginx reverse proxies (X-Accel-Buffering)
        - Standard SSE clients
    """
    default_headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",  # Disable nginx buffering for real-time streaming
    }
    if headers:
        default_headers.update(headers)

    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers=default_headers,
    )
