"""SSE streaming utilities for response bus."""

import json
from typing import Any, AsyncGenerator, Dict, Optional

from fastapi.responses import StreamingResponse


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
    # Subscribe to new messages
    message_queue: list = []

    async def message_callback(message: Any) -> None:
        """Callback to receive new messages."""
        if interaction_id and message.interaction_id != interaction_id:
            return
        message_queue.append(message)

    await response_bus.subscribe(session_id, message_callback)

    try:
        # Send any existing messages first
        existing_messages = await response_bus.get_messages(session_id)
        for message in existing_messages:
            if interaction_id and message.interaction_id != interaction_id:
                continue
            yield format_sse_chunk(message.to_dict())

        # Stream new messages as they arrive
        while True:
            if message_queue:
                message = message_queue.pop(0)
                yield format_sse_chunk(message.to_dict())
            else:
                # Small delay to avoid busy waiting
                import asyncio

                await asyncio.sleep(0.1)
    finally:
        # Cleanup subscription
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
    """
    default_headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
    }
    if headers:
        default_headers.update(headers)

    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers=default_headers,
    )

