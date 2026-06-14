"""WhatsApp voice response filter for TTS synthesis."""

import logging
from typing import TYPE_CHECKING, List, Optional

from jvagent.action.response.channel_filter import ChannelFilter
from jvagent.action.response.message import ResponseMessage

if TYPE_CHECKING:
    from jvagent.action.whatsapp.whatsapp_action import WhatsAppAction

logger = logging.getLogger(__name__)


class WhatsAppVoiceResponseFilter(ChannelFilter):
    """Channel filter that invokes TTS and sets media_url when respond_with_voice.

    When a message has metadata respond_with_voice=True (e.g. from a PTT interaction),
    this filter synthesizes the content via TTS, sets media_url and media_type=voice
    on the message, and clears content so the adapter sends only the voice message.
    """

    def __init__(
        self,
        action: "WhatsAppAction",
        channels: Optional[List[str]] = None,
        priority: int = 105,
    ):
        """Initialize the filter.

        Args:
            action: WhatsAppAction instance for tts_action and get_action
            channels: Channel names (defaults to ["whatsapp"])
            priority: Execution order (runs after WhatsAppFilter at 100)
        """
        if channels is None:
            channels = ["whatsapp"]
        super().__init__(channels=channels, priority=priority)
        self.action = action

    async def filter(self, message: ResponseMessage) -> None:
        """If respond_with_voice, invoke TTS and set media_url/media_type."""
        if message.metadata.get("respond_with_voice") is not True:
            return

        if not message.content or not self.action.tts_action:
            return

        try:
            tts_action = await self.action.get_action(self.action.tts_action)
            if not tts_action:
                logger.debug(
                    "WhatsAppVoiceResponseFilter: TTS action %s not found",
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
                "WhatsAppVoiceResponseFilter: TTS synthesis failed, falling back to text: %s",
                e,
                exc_info=True,
            )
