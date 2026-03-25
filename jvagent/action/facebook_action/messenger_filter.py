"""Messenger channel filter (WhatsApp-style formatting for plain text)."""

from typing import List, Optional

from jvagent.action.response.channel_filter import ChannelFilter
from jvagent.action.response.message import ResponseMessage


class MessengerFilter(ChannelFilter):
    """Transform markdown/HTML to plain text patterns suitable for Messenger."""

    def __init__(
        self, channels: Optional[List[str]] = None, priority: int = 100
    ) -> None:
        if channels is None:
            channels = ["messenger"]
        super().__init__(channels=channels, priority=priority)

    async def filter(self, message: ResponseMessage) -> None:
        if not message.content:
            return
        message.content = (
            message.content.replace("**", "*")
            .replace("<br/>", "\n")
            .replace("<b>", "*")
            .replace("</b>", "*")
        )
