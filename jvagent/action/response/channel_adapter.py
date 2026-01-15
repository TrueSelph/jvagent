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

    The adapter system provides automatic message delivery:
    - Adapters register themselves with ResponseBus via initialize()
    - ResponseBus automatically subscribes adapters to sessions when messages
      are published for their channel (lazy subscription)
    - No manual wiring required in InteractWalker or elsewhere

    Usage:
        1. Create adapter instance in your Action's on_register() method
        2. Call await adapter.initialize() to register with ResponseBus
        3. Messages published with matching channel are automatically delivered

    Example:
        class MyAction(Action):
            async def on_register(self):
                adapter = MyChannelAdapter(channel="mychannel", action=self)
                await adapter.initialize()

    Subclasses must implement:
    - handle_message(): Process incoming messages from response bus
    - send_to_destination(): Send message to external API
    """

    def __init__(self, channel: str, response_bus: Optional[ResponseBus] = None):
        """Initialize channel adapter.

        Args:
            channel: Channel name this adapter handles (e.g., "whatsapp", "web")
            response_bus: Optional ResponseBus instance (can be set later via initialize())
        """
        self.channel = channel
        self.response_bus = response_bus
        self._subscribed_sessions: set = set()
        self._initialized: bool = False

    async def subscribe_to_bus(self, response_bus: ResponseBus) -> None:
        """Subscribe to response bus for message delivery.

        Args:
            response_bus: ResponseBus instance to subscribe to
        """
        self.response_bus = response_bus

    async def initialize(self) -> None:
        """Initialize the channel adapter by getting ResponseBus and registering itself.

        This method should be called after instantiation to:
        1. Get the ResponseBus instance from App
        2. Subscribe to the response bus
        3. Register itself with the response bus for automatic session subscription

        This is typically called from an action's on_register() method.
        """
        if self._initialized:
            return
        
        # Get ResponseBus from App
        try:
            from jvagent.core.app import App
            app = await App.get()
            if app:
                response_bus = await app.get_response_bus()
                if response_bus:
                    await self.subscribe_to_bus(response_bus)
                    await response_bus.register_channel_adapter(self)
                    self._initialized = True
                    logger.info(f"ChannelAdapter for channel '{self.channel}' initialized and registered")
                else:
                    logger.warning(f"ChannelAdapter for channel '{self.channel}': ResponseBus not available")
            else:
                logger.warning(f"ChannelAdapter for channel '{self.channel}': App not available")
        except Exception as e:
            logger.error(
                f"Error initializing ChannelAdapter for channel '{self.channel}': {e}",
                exc_info=True,
            )

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

