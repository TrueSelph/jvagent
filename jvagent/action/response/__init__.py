"""Response bus subsystem for flexible communication.

This module provides the response bus infrastructure for jvagent, enabling:
- Streamed responses via Server-Sent Events (SSE)
- Consolidated responses (synchronous, end-of-walk)
- Adhoc responses (multiple responses to same utterance)
- Multiple destinations (WhatsApp, web, etc.)
"""

from jvagent.action.response.channel_adapter import ChannelAdapter
from jvagent.action.response.message import ResponseMessage
from jvagent.action.response.response_bus import ResponseBus
from jvagent.action.response.streaming import (
    create_sse_response,
    format_sse_chunk,
    stream_messages,
)
from jvagent.action.response.whatsapp_adapter import WhatsAppAdapter

__all__ = [
    "ResponseMessage",
    "ResponseBus",
    "ChannelAdapter",
    "WhatsAppAdapter",
    "create_sse_response",
    "format_sse_chunk",
    "stream_messages",
]

