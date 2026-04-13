"""Response bus subsystem for flexible communication.

This module provides the response bus infrastructure for jvagent, enabling:
- Streamed responses via Server-Sent Events (SSE)
- Consolidated responses (synchronous, end-of-walk)
- Adhoc responses (multiple responses to same utterance)
- Multiple destinations (WhatsApp, web, etc.)
"""

from jvagent.action.response.channel_adapter import ChannelAdapter
from jvagent.action.response.channel_filter import ChannelFilter
from jvagent.action.response.message import ResponseMessage
from jvagent.action.response.response_bus import ResponseBus
from jvagent.action.response.streaming import (
    create_sse_response,
    format_sse_chunk,
    stream_messages,
)

__all__ = [
    "ResponseMessage",
    "ResponseBus",
    "ChannelAdapter",
    "ChannelFilter",
    "create_sse_response",
    "format_sse_chunk",
    "stream_messages",
]
