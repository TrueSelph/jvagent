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
            channel: Channel name this adapter handles (e.g., "whatsapp", "web")
        """
        self.channel = channel
        self.response_bus: Optional[ResponseBus] = None
        self._initialized: bool = False

    async def initialize(self) -> bool:
        """Initialize the channel adapter by getting ResponseBus and registering itself.

        This method should be called after instantiation to:
        1. Get the ResponseBus instance from App
        2. Register itself with the response bus

        This is typically called from an action's on_register() method.
        Callers may rely on the return value for error handling (e.g., log or skip
        registration when False).

        Returns:
            True if initialization and registration succeeded, False otherwise
            (e.g., App or ResponseBus not available).
        """
        if self._initialized:
            return True
        
        # Get ResponseBus from App
        try:
            from jvagent.core.app import App
            app = await App.get()
            if app:
                response_bus = await app.get_response_bus()
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
            else:
                logger.warning(
                    f"ChannelAdapter for channel '{self.channel}': App not available"
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
            True if message was sent successfully, False otherwise
        """
        pass
