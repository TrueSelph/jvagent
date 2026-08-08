"""Facebook Page reaction channel adapter for the response bus.

Awareness-only adapter: reactions are ingested into the agent pipeline for
context (e.g. moderation triggers), but the agent does **not** auto-reply
or auto-react.  ``send()`` logs and returns ``True`` without any Graph API
call.  Custom tools/actions can inspect ``message.metadata`` for
``reaction_type``, ``post_id``, ``comment_id`` etc. if they want to act.
"""

import logging
from typing import Any

from jvagent.action.response.channel_adapter import ChannelAdapter
from jvagent.action.response.message import ResponseMessage

logger = logging.getLogger(__name__)


class FacebookReactionAdapter(ChannelAdapter):
    """No-op adapter for ``channel="facebook_reaction"``.

    The agent receives reaction events for awareness but does not deliver
    any outbound response.  ``send()`` silently succeeds so the response
    bus considers the message delivered.
    """

    def __init__(self, action: Any = None) -> None:
        super().__init__(channel="facebook_reaction")
        self.action = action

    async def send(self, message: ResponseMessage) -> bool:
        logger.debug(
            "FacebookReactionAdapter: awareness-only, not replying to reaction "
            "(message_id=%s, reaction_type=%s)",
            message.id,
            message.metadata.get("reaction_type", ""),
        )
        return True
