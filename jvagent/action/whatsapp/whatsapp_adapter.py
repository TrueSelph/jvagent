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

    This adapter is automatically created and registered by WhatsAppAction
    in its on_register() method. Messages published with channel="whatsapp"
    are automatically delivered to this adapter.

    Example usage in action:
        class WhatsAppAction(Action):
            async def on_register(self):
                adapter = WhatsAppAdapter(channel="whatsapp", action=self)
                await adapter.initialize()
                self._channel_adapter = adapter
    """

    def __init__(
        self,
        channel: str = "whatsapp",
        action: Any = None,
        response_bus: Optional[ResponseBus] = None,
    ):
        """Initialize WhatsApp adapter.

        Args:
            channel: Channel name (default: "whatsapp")
            action: WhatsAppAction instance
            response_bus: Optional ResponseBus instance
        """
        super().__init__(channel, response_bus)
        self.action = action

    async def handle_message(self, message: ResponseMessage) -> None:
        """Handle incoming message from response bus.

        Handles adhoc and final messages (complete streamed responses).
        Does not handle stream chunks (receive_chunks=False by default).

        Args:
            message: ResponseMessage object
        """
        # TESTING: Log when handle_message is called
        logger.warning(
            f"WhatsAppAdapter: handle_message CALLED - message_id={message.id}, "
            f"message_type={message.message_type}, channel={self.channel}"
        )
        
        if not self.should_handle(message):
            logger.warning(
                f"WhatsAppAdapter: handle_message - should_handle returned False for message_id={message.id}"
            )
            return

        # Send adhoc and final messages to WhatsApp
        # Final messages contain complete streamed responses
        if message.message_type in ("adhoc", "final"):
            logger.warning(
                f"WhatsAppAdapter: handle_message - About to call send_to_destination for message_id={message.id}, "
                f"message_type={message.message_type}"
            )
            success = await self.send_to_destination(message)
            logger.warning(
                f"WhatsAppAdapter: handle_message - send_to_destination returned success={success} for message_id={message.id}"
            )
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
        # TESTING: Log when send_to_destination is called
        logger.warning(
            f"WhatsAppAdapter: send_to_destination CALLED - message_id={message.id}, "
            f"message_type={message.message_type}, channel={self.channel}"
        )
        logger.warning(
            f"WhatsAppAdapter: send_to_destination - message_content={message.content[:100] if message.content else None}, "
            f"metadata={message.metadata}"
        )
        api_url = self.action.api_url if self.action else None
        api_key = self.action.api_key if self.action else None
        logger.warning(
            f"WhatsAppAdapter: send_to_destination - api_url={api_url}, api_key_configured={bool(api_key)}"
        )
        
        if not self.action:
            logger.warning("WhatsAppAdapter: Cannot send - no action instance")
            return False
        
        # TESTING: Comment out actual implementation
        # if not api_url or not api_key:
        #     logger.warning(
        #         "WhatsAppAdapter: Cannot send - api_url or api_key not configured"
        #     )
        #     return False

        # try:
        #     import aiohttp

        #     # Extract recipient from message metadata
        #     recipient = message.metadata.get("recipient")
        #     if not recipient:
        #         logger.warning(
        #             f"WhatsAppAdapter: No recipient in message metadata for {message.id}"
        #         )
        #         return False

        #     # Prepare WhatsApp API payload
        #     payload = {
        #         "to": recipient,
        #         "type": "text",
        #         "text": {"body": message.content},
        #     }

        #     headers = {
        #         "Authorization": f"Bearer {self.api_key}",
        #         "Content-Type": "application/json",
        #     }

        #     # Send to WhatsApp API
        #     async with aiohttp.ClientSession() as session:
        #         async with session.post(
        #             self.api_url, json=payload, headers=headers
        #         ) as response:
        #             if response.status == 200:
        #                 logger.info(
        #                     f"WhatsAppAdapter: Successfully sent message {message.id} to {recipient}"
        #                 )
        #                 return True
        #             else:
        #                 error_text = await response.text()
        #                 logger.error(
        #                     f"WhatsAppAdapter: Failed to send message {message.id}: "
        #                     f"HTTP {response.status} - {error_text}"
        #                 )
        #                 return False

        # except Exception as e:
        #     logger.error(
        #         f"WhatsAppAdapter: Error sending message {message.id} to WhatsApp: {e}",
        #         exc_info=True,
        #     )
        #     return False
        
        # TESTING: Return True to simulate successful send
        logger.warning(f"WhatsAppAdapter: send_to_destination COMPLETED (test mode) - message_id={message.id}")
        return True

