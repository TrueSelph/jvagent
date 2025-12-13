"""Channel adapter interface for response bus subscribers."""

import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

from jvagent.action.response.message import ResponseMessage
from jvagent.action.response.response_bus import ResponseBus

logger = logging.getLogger(__name__)


class ChannelAdapter(ABC):
    """Base class for channel adapters that subscribe to response bus.

    Channel adapters receive messages from the response bus and deliver them
    to external destinations (WhatsApp, web, etc.).

    Subclasses should implement:
    - subscribe_to_bus(): Subscribe to response bus
    - handle_message(): Process incoming messages
    - send_to_destination(): Send message to external API
    """

    def __init__(self, channel: str, response_bus: Optional[ResponseBus] = None):
        """Initialize channel adapter.

        Args:
            channel: Channel name this adapter handles (e.g., "whatsapp", "web")
            response_bus: Optional ResponseBus instance (can be set later)
        """
        self.channel = channel
        self.response_bus = response_bus
        self._subscribed_sessions: set = set()

    async def subscribe_to_bus(self, response_bus: ResponseBus) -> None:
        """Subscribe to response bus for message delivery.

        Args:
            response_bus: ResponseBus instance to subscribe to
        """
        self.response_bus = response_bus

    async def subscribe_to_session(
        self, session_id: str, receive_chunks: bool = False
    ) -> None:
        """Subscribe to messages for a specific session.

        Args:
            session_id: Session identifier
            receive_chunks: If True, receive stream_chunk messages. If False, only receive
                          final and adhoc messages. Default: False
        """
        if not self.response_bus:
            logger.warning(
                f"ChannelAdapter {self.channel}: Cannot subscribe - no response bus"
            )
            return

        if session_id in self._subscribed_sessions:
            return  # Already subscribed

        await self.response_bus.subscribe(
            session_id, self.handle_message, receive_chunks=receive_chunks
        )
        self._subscribed_sessions.add(session_id)

    async def unsubscribe_from_session(self, session_id: str) -> None:
        """Unsubscribe from messages for a specific session.

        Args:
            session_id: Session identifier
        """
        if not self.response_bus:
            return

        if session_id not in self._subscribed_sessions:
            return  # Not subscribed

        await self.response_bus.unsubscribe(session_id, self.handle_message)
        self._subscribed_sessions.discard(session_id)

    @abstractmethod
    async def handle_message(self, message: ResponseMessage) -> None:
        """Handle incoming message from response bus.

        This method is called when a message is published to the bus for
        a session this adapter is subscribed to.

        Message types received depend on subscription preferences:
        - If receive_chunks=True: Individual stream_chunk messages (for real-time streaming)
        - Always: Final complete messages when stream is finalized
        - Always: Adhoc messages

        Args:
            message: ResponseMessage object
        """
        pass

    @abstractmethod
    async def send_to_destination(
        self, message: ResponseMessage
    ) -> bool:
        """Send message to external destination.

        Args:
            message: ResponseMessage object to send

        Returns:
            True if message was sent successfully, False otherwise
        """
        pass

    def should_handle(self, message: ResponseMessage) -> bool:
        """Check if this adapter should handle the message.

        Default implementation checks if message channel matches adapter channel.

        Args:
            message: ResponseMessage to check

        Returns:
            True if adapter should handle this message
        """
        return message.channel == self.channel

