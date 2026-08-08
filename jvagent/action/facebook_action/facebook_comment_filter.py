"""Facebook Page comment channel filter for the response bus.

Strips markdown and HTML formatting that is inappropriate for public
Facebook comments (plain text only), and truncates to the Facebook
comment length limit.
"""

import logging
from typing import List, Optional

from jvagent.action.response.channel_filter import ChannelFilter
from jvagent.action.response.message import ResponseMessage

from .facebook_comment_text import to_facebook_comment_text

logger = logging.getLogger(__name__)


class FacebookCommentFilter(ChannelFilter):
    """Transform markdown/HTML to plain text suitable for Facebook Page comments.

    Facebook comments are plain text — no markdown, no HTML.  This filter
    strips common formatting artifacts left by the ReplyAction voice. The
    transformation lives in ``facebook_comment_text`` so the adapter applies
    exactly the same one.
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
        message.content = to_facebook_comment_text(str(message.content))
