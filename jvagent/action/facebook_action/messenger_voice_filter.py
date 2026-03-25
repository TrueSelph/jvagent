"""Messenger voice response filter for TTS synthesis (WhatsApp parity)."""

import logging
from typing import TYPE_CHECKING, List, Optional

from jvagent.action.response.channel_filter import ChannelFilter
from jvagent.action.response.message import ResponseMessage

if TYPE_CHECKING:
    from jvagent.action.facebook_action.facebook_action import FacebookAction

logger = logging.getLogger(__name__)


class MessengerVoiceResponseFilter(ChannelFilter):
    """When ``respond_with_voice`` is True, synthesize reply via TTS and send as audio."""

    def __init__(
        self,
        action: "FacebookAction",
        channels: Optional[List[str]] = None,
        priority: int = 105,
    ) -> None:
        if channels is None:
            channels = ["messenger"]
        super().__init__(channels=channels, priority=priority)
        self.action = action

    async def filter(self, message: ResponseMessage) -> None:
        if message.metadata.get("respond_with_voice") is not True:
            return
        if not message.content or not self.action.tts_action:
            return
        try:
            tts_action = await self.action.get_action(self.action.tts_action)
            if not tts_action:
                logger.debug(
                    "MessengerVoiceResponseFilter: TTS action %s not found",
                    self.action.tts_action,
                )
                return
            url = await tts_action.invoke(message.content, as_url=True)
            if url:
                message.metadata["media_url"] = url
                message.metadata["media_type"] = "voice"
                message.content = ""
        except Exception as e:
            logger.warning(
                "MessengerVoiceResponseFilter: TTS failed, falling back to text: %s",
                e,
                exc_info=True,
            )
