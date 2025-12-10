"""WhatsApp channel adapter example for response bus."""

import logging
from typing import Any, Optional

from jvagent.action.response.channel_adapter import ChannelAdapter
from jvagent.action.response.message import ResponseMessage
from jvagent.action.response.response_bus import ResponseBus

logger = logging.getLogger(__name__)


class WhatsAppAdapter(ChannelAdapter):
    """WhatsApp channel adapter for response bus.

    This adapter subscribes to the response bus and sends adhoc messages
    to WhatsApp via the WhatsApp API.

    Example usage:
        bus = await agent.get_response_bus()
        adapter = WhatsAppAdapter(
            channel="whatsapp",
            api_url="https://api.whatsapp.com/v1/messages",
            api_key="your_api_key"
        )
        await adapter.subscribe_to_bus(bus)
        await adapter.subscribe_to_session(session_id)
    """

    def __init__(
        self,
        channel: str = "whatsapp",
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        response_bus: Optional[ResponseBus] = None,
    ):
        """Initialize WhatsApp adapter.

        Args:
            channel: Channel name (default: "whatsapp")
            api_url: WhatsApp API URL
            api_key: WhatsApp API key
            response_bus: Optional ResponseBus instance
        """
        super().__init__(channel, response_bus)
        self.api_url = api_url
        self.api_key = api_key

    async def handle_message(self, message: ResponseMessage) -> None:
        """Handle incoming message from response bus.

        Only handles adhoc messages (not stream chunks or final messages).

        Args:
            message: ResponseMessage object
        """
        if not self.should_handle(message):
            return

        # Only send adhoc messages to WhatsApp
        # Stream chunks and final messages are handled by SSE endpoint
        if message.message_type == "adhoc":
            success = await self.send_to_destination(message)
            if success:
                message.mark_delivered()
            else:
                logger.warning(
                    f"WhatsAppAdapter: Failed to send message {message.id} to WhatsApp"
                )

    async def send_to_destination(self, message: ResponseMessage) -> bool:
        """Send message to WhatsApp API.

        Args:
            message: ResponseMessage to send

        Returns:
            True if message was sent successfully, False otherwise
        """
        if not self.api_url or not self.api_key:
            logger.warning(
                "WhatsAppAdapter: Cannot send - api_url or api_key not configured"
            )
            return False

        try:
            import aiohttp

            # Extract recipient from message metadata
            recipient = message.metadata.get("recipient")
            if not recipient:
                logger.warning(
                    f"WhatsAppAdapter: No recipient in message metadata for {message.id}"
                )
                return False

            # Prepare WhatsApp API payload
            payload = {
                "to": recipient,
                "type": "text",
                "text": {"body": message.content},
            }

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }

            # Send to WhatsApp API
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.api_url, json=payload, headers=headers
                ) as response:
                    if response.status == 200:
                        logger.info(
                            f"WhatsAppAdapter: Successfully sent message {message.id} to {recipient}"
                        )
                        return True
                    else:
                        error_text = await response.text()
                        logger.error(
                            f"WhatsAppAdapter: Failed to send message {message.id}: "
                            f"HTTP {response.status} - {error_text}"
                        )
                        return False

        except Exception as e:
            logger.error(
                f"WhatsAppAdapter: Error sending message {message.id} to WhatsApp: {e}",
                exc_info=True,
            )
            return False

