"""SSE streaming utilities for response bus."""

import asyncio
import json
import logging
from typing import Any, AsyncGenerator, Dict, Optional

from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)


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
) -> AsyncGenerator[str, None]:
    """Stream messages from response bus for a session.

    This generator yields SSE-formatted chunks as messages are published
    to the response bus for the given session.

    Args:
        session_id: Session identifier
        response_bus: ResponseBus instance
        interaction_id: Optional interaction ID to filter messages

    Yields:
        SSE-formatted string chunks
    """
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

    # Subscribe with receive_chunks=True to get all stream chunks
    await response_bus.subscribe(session_id, message_callback, receive_chunks=True)

    try:
        # Send any existing messages first
        existing_messages = await response_bus.get_messages(session_id)
        for message in existing_messages:
            if interaction_id and message.interaction_id != interaction_id:
                continue
            yield format_sse_chunk(message.to_dict())

        # Stream new messages as they arrive using queue-based waiting
        while True:
            try:
                # Wait for message with timeout to allow checking for done event
                message = await asyncio.wait_for(message_queue.get(), timeout=0.1)
                yield format_sse_chunk(message.to_dict())
            except asyncio.TimeoutError:
                # Check if we should continue (allows graceful shutdown)
                if done.is_set():
                    break
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

