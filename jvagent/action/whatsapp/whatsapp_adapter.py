"""WhatsApp channel adapter example for response bus."""

import asyncio
import logging
from typing import Any, Dict, Optional, List
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
    
    WhatsApp uses non-streaming mode (stream=False), so messages are published
    as complete 'adhoc' messages. The adapter ignores 'final' and 'stream_chunk'
    messages since WhatsApp doesn't use streaming.

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
        # Per-user locks to serialize message sends and ensure ordering
        self._user_locks: Dict[str, asyncio.Lock] = {}

    def _get_user_lock(self, user_id: str) -> asyncio.Lock:
        """Get or create a lock for a specific user to serialize message sends.
        
        Implements LRU-style eviction to prevent unbounded memory growth.
        Keeps at most 1000 locks cached.
        """
        if user_id not in self._user_locks:
            # Evict oldest locks if we exceed the limit
            if len(self._user_locks) >= 1000:
                # Remove the oldest 100 entries to avoid frequent evictions
                keys_to_remove = list(self._user_locks.keys())[:100]
                for key in keys_to_remove:
                    del self._user_locks[key]
            self._user_locks[user_id] = asyncio.Lock()
        return self._user_locks[user_id]

    async def handle_message(self, message: ResponseMessage) -> None:
        """Handle incoming message from response bus.

        Only handles 'adhoc' messages - ignores 'final' and 'stream_chunk' messages.
        WhatsApp uses non-streaming mode (stream=False), so complete responses
        are published as 'adhoc' messages.

        Args:
            message: ResponseMessage object
        """
        try:
            if not self.should_handle(message):
                return
                
            # Skip if action is not configured
            if self.action and not self.action.is_configured():
                logger.debug("WhatsAppAdapter: Skipping message - WhatsApp action is not configured")
                return

            # Only handle adhoc messages - WhatsApp uses non-streaming mode
            # Ignore 'final' and 'stream_chunk' messages
            if message.message_type != "adhoc":
                return
            
            # Fetch interaction once and reuse
            interaction = None
            if message.interaction_id:
                interaction = await Interaction.get(message.interaction_id)
            
            # Trigger typing status
            if interaction and interaction.user_id and self.action:
                try:
                    await self.action.set_typing(interaction.user_id, value=True)
                except Exception as e:
                    logger.debug(f"WhatsAppAdapter: Failed to set typing status: {e}")

            # Send message to WhatsApp
            success = await self.send_to_destination(message, interaction)
            
            # Clear typing status after message is sent
            if interaction and interaction.user_id and self.action:
                try:
                    await self.action.set_typing(interaction.user_id, value=False)
                except Exception as e:
                    logger.debug(f"WhatsAppAdapter: Failed to clear typing status: {e}")

            if success:
                message.mark_delivered()
            else:
                logger.warning(f"WhatsAppAdapter: Failed to send message {message.id} to WhatsApp")

        except Exception as e:
            # Catch-all to prevent errors from bubbling up to ResponseBus
            logger.warning(f"WhatsAppAdapter: Error handling message {message.id}: {e}")

    async def send_to_destination(
        self, 
        message: ResponseMessage, 
        interaction: Optional[Interaction] = None
    ) -> bool:
        """Send message to WhatsApp API.

        Args:
            message: ResponseMessage to send
            interaction: Optional pre-fetched Interaction (avoids duplicate fetch)

        Returns:
            True if message was sent successfully, False otherwise
        """
        if not self.action:
            logger.warning("WhatsAppAdapter: Cannot send - no action instance")
            return False

        # Check if action is configured
        if not self.action.is_configured():
            logger.debug(
                "WhatsAppAdapter: Cannot send - WhatsApp action is not configured. "
                "Set WHATSAPP_API_URL and WHATSAPP_API_KEY environment variables."
            )
            return False

        # Get interaction if not provided
        if interaction is None:
            interaction = await Interaction.get(message.interaction_id)
        if not interaction:
            logger.warning("WhatsAppAdapter: Cannot send message - no interaction")
            return False

        # Chunk message
        sanitized_message = self.sanitize_message(message.content)
        chunks = self.chunk_long_message(sanitized_message, max_length=self.action.chunk_length, chunk_length=self.action.chunk_length)

        # Use per-user lock to serialize sends and ensure message ordering
        user_lock = self._get_user_lock(interaction.user_id)
        
        async with user_lock:
            try:
                for chunk in chunks:
                    await self.action.api().send_message(
                        phone=interaction.user_id,
                        message=chunk,
                    )
            except Exception as e:
                logger.warning(f"WhatsAppAdapter: Failed to send message to WhatsApp: {e}")
                return False

            logger.debug(f"WhatsAppAdapter: Message sent successfully to {interaction.user_id}")
            return True



    async def subscribe_to_session(
        self, session_id: str, receive_chunks: bool = False
    ) -> None:
        """Subscribe to messages for a specific session.

        Note: Always subscribes with receive_chunks=True for compatibility with the
        base ChannelAdapter, even though WhatsApp only processes 'adhoc' messages.
        Stream chunks and final messages are ignored in handle_message().

        Args:
            session_id: Session identifier
            receive_chunks: Ignored for WhatsApp adapter
        """
        await super().subscribe_to_session(session_id, receive_chunks=True)

    def sanitize_message(self, message: str) -> str:
        """Sanitize message content for WhatsApp formatting.
        
        Converts markdown and HTML formatting to WhatsApp-compatible formatting:
        - ** (bold markdown) -> * (WhatsApp bold)
        - <br/>, <b>, </b> HTML tags -> WhatsApp equivalents
        
        Args:
            message: Raw message content
            
        Returns:
            Sanitized message with WhatsApp-compatible formatting
        """
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
            # Calculate space needed (1 for space separator, except for first word)
            space_needed = 1 if current_chunk else 0

            if current_chunk_length + word_length + space_needed <= chunk_length:
                # Add the word to the current chunk
                if current_chunk:
                    current_chunk += " "
                    current_chunk_length += 1  # Account for space
                current_chunk += word
                current_chunk_length += word_length
            else:
                # If the current chunk is full, add it to the list of chunks
                if current_chunk:  # Only add non-empty chunks
                    final_chunks.append(current_chunk)
                current_chunk = word  # Start a new chunk with the current word
                current_chunk_length = word_length

        if current_chunk:
            # Add the last chunk if it's non-empty
            final_chunks.append(current_chunk)

        return final_chunks
