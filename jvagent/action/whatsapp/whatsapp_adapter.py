"""WhatsApp channel adapter example for response bus."""

import logging
from typing import Any, Optional, List
import re

from jvagent.action.response.channel_adapter import ChannelAdapter
from jvagent.action.response.message import ResponseMessage
from jvagent.action.response.response_bus import ResponseBus
from jvagent.memory import Interaction

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
        self._typing_phones = set()  # phone numbers set to track typing status

    async def handle_message(self, message: ResponseMessage) -> None:
        """Handle incoming message from response bus.

        Handles adhoc, stream chunks, and final messages.

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

        # Trigger typing status for any message type if it's the start of an interaction response
        if message.interaction_id:
            interaction = await Interaction.get(message.interaction_id)
            if interaction and interaction.user_id:
                await self.set_typing(interaction.user_id, value=True)

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
            
            # Clear typing status after message is sent ONLY for "final" messages
            if message.interaction_id and message.message_type == "final":
                interaction = await Interaction.get(message.interaction_id)
                if interaction and interaction.user_id:
                    await self.set_typing(interaction.user_id, value=False)

            if success:
                message.mark_delivered()
            else:
                logger.warning(f"WhatsAppAdapter: Failed to send message {message.id} to WhatsApp")
        
        elif message.message_type == "stream_chunk":
            # typing already triggered at the top of handle_message
            pass

    async def send_to_destination(self, message: ResponseMessage) -> bool:
        """Send message to WhatsApp API.

        Args:
            message: ResponseMessage to send

        Returns:
            True if message was sent successfully, False otherwise
        """
        api_url = self.action.api_url if self.action else None
        api_key = self.action.api_key if self.action else None

        if not self.action:
            logger.warning("WhatsAppAdapter: Cannot send - no action instance")
            return False

        # TESTING: Comment out actual implementation
        if not api_url or not api_key:
            logger.warning("WhatsAppAdapter: Cannot send - api_url or api_key not configured")
            return False

        # Get interaction
        interaction = await Interaction.get(message.interaction_id)
        if not interaction:
            logger.warning("WhatsAppAdapter: Cannot send message - no interaction")
            return False

        # Chunk message
        sanitized_message = self.sanitize_message(message.content)
        chunks = self.chunk_long_message(sanitized_message)

        for chunk in chunks:
            ss = await self.action.api().send_message(
                phone=interaction.user_id,
                message=chunk,
            )
            logger.warning("ss")
            logger.warning(ss)

        logger.warning(f"WhatsAppAdapter: send_to_destination COMPLETED - message_id={message.id}")
        return True

    async def set_typing(self, phone: str, value: bool = True) -> None:
        """Set or clear typing status for a phone number.
        
        Args:
            phone: Phone number
            value: True to start typing, False to stop
        """
        if not self.action:
            return

        if value:
            if phone in self._typing_phones:
                return  # Already typing
            self._typing_phones.add(phone)
        else:
            if phone not in self._typing_phones:
                return  # Not typing
            self._typing_phones.discard(phone)

        try:
            logger.info(f"WhatsAppAdapter: Setting typing status to {value} for user {phone}")
            await self.action.api().set_typing_status(
                phone=phone,
                value=value
            )
        except Exception as e:
            logger.warning(f"WhatsAppAdapter: Failed to set typing status for {phone}: {e}")

    async def subscribe_to_session(
        self, session_id: str, receive_chunks: bool = False
    ) -> None:
        """Subscribe to messages for a specific session, always requesting chunks.

        Args:
            session_id: Session identifier
            receive_chunks: Ignored, always set to True
        """
        await super().subscribe_to_session(session_id, receive_chunks=True)

    def sanitize_message(self, message: str) -> str:
        return (
            message.replace("**", "*")
            .replace("<br/>", "\n")
            .replace("<b>", "*")
            .replace("</b>", "*")
        )

    def chunk_long_message(
        self, message: str, max_length: int = 1024, chunk_length: int = 1024
    ) -> List[str]:
        """
        Splits a long message into smaller chunks of no more than chunk_length characters,
        ensuring no single chunk exceeds max_length.

        Args:
            message: The text to chunk
            max_length: Maximum allowed length for any chunk
            chunk_length: Target length for chunks

        Returns:
            List of message chunks
        """
        if len(message) <= max_length:
            return [message]

        # Initialize variables
        final_chunks = []
        current_chunk = ""
        current_chunk_length = 0

        # Split the message into words while preserving newline characters
        words = re.findall(r"\S+\n*|\n+", message)
        words = [word for word in words if word.strip()]  # Filter out empty strings

        for word in words:
            word_length = len(word)

            if current_chunk_length + word_length + 1 <= chunk_length:
                # Add the word to the current chunk
                if current_chunk:
                    current_chunk += " "
                current_chunk += word
                current_chunk_length += word_length + 1
            else:
                # If the current chunk is full, add it to the list of chunks
                final_chunks.append(current_chunk)
                current_chunk = word  # Start a new chunk with the current word
                current_chunk_length = word_length

        if current_chunk:
            # Add the last chunk if it's non-empty
            final_chunks.append(current_chunk)

        return final_chunks
