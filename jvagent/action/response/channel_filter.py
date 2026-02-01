"""Channel filter interface for response bus message transformation."""

import logging
from abc import ABC, abstractmethod
from typing import List, Optional

from jvagent.action.response.message import ResponseMessage
from jvagent.action.response.response_bus import ResponseBus

logger = logging.getLogger(__name__)


class ChannelFilter(ABC):
    """Base class for channel filters that transform messages before delivery.

    Channel filters register themselves with ResponseBus and transform adhoc messages
    before they are delivered to channel adapters. Multiple filters can be registered
    for the same channel and will execute in priority order (lower priority first).

    Usage:
        1. Create filter instance in your Action's on_register() method
        2. Call await filter.initialize() to register with ResponseBus
        3. Messages published with matching channel are automatically transformed via filter()

    Example:
        class MyAction(Action):
            async def on_register(self):
                filter = MyChannelFilter(channels=["mychannel"], priority=100)
                await filter.initialize()

    Subclasses must implement:
    - filter(): Transform message content in-place
    """

    def __init__(self, channels: List[str], priority: int = 100, fail_fast: bool = False):
        """Initialize channel filter.

        Args:
            channels: List of channel names this filter handles (e.g., ["whatsapp", "web"])
            priority: Execution order (lower numbers execute first, default 100)
            fail_fast: If True, filter errors halt the chain and skip delivery
        """
        self.channels = channels
        self.priority = priority
        self.fail_fast = fail_fast
        self.response_bus: Optional[ResponseBus] = None
        self._initialized: bool = False

    async def initialize(self) -> bool:
        """Initialize the channel filter by getting ResponseBus and registering itself.

        This method should be called after instantiation to:
        1. Get the ResponseBus instance from App
        2. Register itself with the response bus

        This is typically called from an action's on_register() method.

        Returns:
            True if initialization and registration succeeded, False otherwise
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
                    await response_bus.register_channel_filter(self)
                    self._initialized = True
                    channel_list = ", ".join(self.channels)
                    logger.info(
                        f"ChannelFilter for channels [{channel_list}] initialized and registered "
                        f"(priority: {self.priority})"
                    )
                    return True
                else:
                    logger.warning(
                        f"ChannelFilter for channels {self.channels}: ResponseBus not available"
                    )
                    return False
            else:
                logger.warning(
                    f"ChannelFilter for channels {self.channels}: App not available"
                )
                return False
        except Exception as e:
            logger.error(
                f"Error initializing ChannelFilter for channels {self.channels}: {e}",
                exc_info=True,
            )
            return False

    def applies_to_channel(self, channel: str) -> bool:
        """Check if this filter applies to a specific channel.

        Args:
            channel: Channel name to check

        Returns:
            True if filter applies to the channel, False otherwise
        """
        return channel in self.channels

    @abstractmethod
    async def filter(self, message: ResponseMessage) -> None:
        """Transform message content in-place.

        This method is called by ResponseBus before routing messages to channel adapters.
        The filter should modify message.content directly to transform the message.

        Args:
            message: ResponseMessage object to transform (modified in-place)
        """
        pass
