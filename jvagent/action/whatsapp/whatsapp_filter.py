"""WhatsApp channel filter for message transformation."""

import logging
from typing import List

from jvagent.action.response.channel_filter import ChannelFilter
from jvagent.action.response.message import ResponseMessage

logger = logging.getLogger(__name__)


class WhatsAppFilter(ChannelFilter):
    """WhatsApp channel filter that transforms message content for WhatsApp formatting.

    This filter converts markdown and HTML formatting to WhatsApp-compatible formatting:
    - ** (bold markdown) -> * (WhatsApp bold)
    - <br/> -> \n (HTML break to newline)
    - <b>, </b> -> * (HTML bold to WhatsApp bold)

    This filter is automatically created and registered by WhatsAppAction
    in its on_register() and on_startup() methods. Messages published with
    channel="whatsapp" are automatically transformed by this filter before
    being delivered to WhatsAppAdapter.

    Example usage in action:
        class WhatsAppAction(Action):
            async def on_register(self):
                filter = WhatsAppFilter(channels=["whatsapp"], priority=100)
                await filter.initialize()
    """

    def __init__(self, channels: List[str] = None, priority: int = 100):
        """Initialize WhatsApp filter.

        Args:
            channels: List of channel names (defaults to ["whatsapp"])
            priority: Execution order (default 100)
        """
        if channels is None:
            channels = ["whatsapp"]
        super().__init__(channels=channels, priority=priority)

    async def filter(self, message: ResponseMessage) -> None:
        """Transform message content for WhatsApp formatting.

        Converts markdown and HTML formatting to WhatsApp-compatible formatting.
        Modifies message.content in-place.

        Args:
            message: ResponseMessage object to transform (modified in-place)
        """
        if not message.content:
            return

        # Apply transformations
        message.content = (
            message.content.replace("**", "*")
            .replace("<br/>", "\n")
            .replace("<b>", "*")
            .replace("</b>", "*")
        )
