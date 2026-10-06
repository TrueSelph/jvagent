"""Channel adapter interface for response bus subscribers."""

import logging
from abc import ABC, abstractmethod
from typing import Optional

from jvagent.action.response.message import ResponseMessage
from jvagent.action.response.response_bus import ResponseBus

logger = logging.getLogger(__name__)


class ChannelAdapter(ABC):
    """Base class for channel adapters that deliver messages to external destinations.

    Channel adapters register themselves with ResponseBus and receive adhoc messages
    directly when published for their channel. No session subscriptions needed.

    Usage:
        1. Create adapter instance in your Action's on_register() method
        2. Call await adapter.initialize() to register with ResponseBus
        3. Messages published with matching channel are automatically delivered via send()

    Example:
        class MyAction(Action):
            async def on_register(self):
                adapter = MyChannelAdapter(channel="mychannel", action=self)
                await adapter.initialize()

    Subclasses must implement:
    - send(): Send message to external destination
    """

    def __init__(self, channel: str):
        """Initialize channel adapter.

        Args:
            channel: Channel name this adapter handles (e.g., "whatsapp", "default")
        """
        self.channel = channel
        self.deliver_thoughts: bool = False
        # The bus may retry an exception only when the transport deduplicates
        # replayed sends by ResponseMessage.id. Legacy adapters default closed.
        self.supports_idempotent_replay: bool = False
        self.response_bus: Optional[ResponseBus] = None
        self._initialized: bool = False

    async def initialize(self, agent=None) -> bool:
        """Initialize the channel adapter by getting ResponseBus and registering itself.

        This method should be called after instantiation to:
        1. Get the ResponseBus instance from the agent
        2. Register itself with the response bus

        This is typically called from an action's on_startup() method.
        Callers must pass the agent (e.g., agent=await self.get_agent()).

        Args:
            agent: Agent node that owns the ResponseBus. Required for agent-scoped registration.

        Returns:
            True if initialization and registration succeeded, False otherwise
            (e.g., agent or ResponseBus not available).
        """
        if self._initialized:
            return True

        if not agent:
            logger.warning(
                f"ChannelAdapter for channel '{self.channel}': agent is required"
            )
            return False

        try:
            response_bus = await agent.get_response_bus()
            if response_bus:
                self.response_bus = response_bus
                await response_bus.register_channel_adapter(self)
                self._initialized = True
                logger.info(
                    f"ChannelAdapter for channel '{self.channel}' initialized and registered"
                )
                return True
            else:
                logger.warning(
                    f"ChannelAdapter for channel '{self.channel}': ResponseBus not available"
                )
                return False
        except Exception as e:
            logger.error(
                f"Error initializing ChannelAdapter for channel '{self.channel}': {e}",
                exc_info=True,
            )
            return False

    @abstractmethod
    async def send(self, message: ResponseMessage) -> bool:
        """Send message to external destination.

        This method is called by ResponseBus when an adhoc message is published
        for this adapter's channel.

        Args:
            message: ResponseMessage object to send

        Returns:
            True when the adapter's configured transport accepts the message,
            False when delivery is rejected or fails. This is not a recipient
            read receipt. For replayable messages, use ``message.id`` as the
            transport's idempotency key when that capability is available.

        Set ``supports_idempotent_replay`` to True only when the outbound
        transport enforces ``message.id`` as an idempotency key. Otherwise an
        exception is treated as an uncertain send and the bus does not retry.
        """
