"""Facebook Page comment channel filter for the response bus.

Strips markdown and HTML formatting that is inappropriate for public
Facebook comments (plain text only), and truncates to the Facebook
comment length limit.
"""

import logging
import re
from typing import List, Optional

from jvagent.action.response.channel_filter import ChannelFilter
from jvagent.action.response.message import ResponseMessage

logger = logging.getLogger(__name__)

FACEBOOK_COMMENT_MAX_LENGTH = 9000


class FacebookCommentFilter(ChannelFilter):
    """Transform markdown/HTML to plain text suitable for Facebook Page comments.

    Facebook comments are plain text — no markdown, no HTML.  This filter
    strips common formatting artifacts left by the ReplyAction voice.
    """

    def __init__(
        self, channels: Optional[List[str]] = None, priority: int = 100
    ) -> None:
        if channels is None:
            channels = ["facebook_comment"]
        super().__init__(channels=channels, priority=priority)

    async def filter(self, message: ResponseMessage) -> None:
        if not message.content:
            return
        text = str(message.content)
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
        text = re.sub(r"\*(.+?)\*", r"\1", text)
        text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        text = (
            text.replace("<br/>", "\n")
            .replace("<br>", "\n")
            .replace("<b>", "")
            .replace("</b>", "")
            .replace("<i>", "")
            .replace("</i>", "")
            .replace("<p>", "\n")
            .replace("</p>", "")
        )
        if len(text) > FACEBOOK_COMMENT_MAX_LENGTH:
            text = text[: FACEBOOK_COMMENT_MAX_LENGTH - 3] + "..."
        message.content = text.strip()
