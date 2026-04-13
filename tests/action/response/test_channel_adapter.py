"""Unit tests for ChannelAdapter."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.response.channel_adapter import ChannelAdapter
from jvagent.action.response.message import ResponseMessage
from jvagent.action.response.response_bus import ResponseBus


class StubChannelAdapter(ChannelAdapter):
    """Concrete adapter for testing ChannelAdapter.initialize()."""

    async def send(self, message: ResponseMessage) -> bool:
        return True


class TestChannelAdapterInitialize:
    """Tests for ChannelAdapter.initialize() return value."""

    @pytest.mark.asyncio
    async def test_initialize_returns_false_when_agent_unavailable(self):
        """initialize() returns False when agent is not provided."""
        adapter = StubChannelAdapter(channel="test")

        result = await adapter.initialize(agent=None)

        assert result is False
        assert adapter._initialized is False

    @pytest.mark.asyncio
    async def test_initialize_returns_false_when_response_bus_unavailable(self):
        """initialize() returns False when ResponseBus is not available."""
        adapter = StubChannelAdapter(channel="test")
        mock_agent = MagicMock()
        mock_agent.get_response_bus = AsyncMock(return_value=None)

        result = await adapter.initialize(agent=mock_agent)

        assert result is False
        assert adapter._initialized is False

    @pytest.mark.asyncio
    async def test_initialize_returns_true_when_success(self):
        """initialize() returns True when agent and ResponseBus are available."""
        adapter = StubChannelAdapter(channel="test")
        mock_response_bus = MagicMock(spec=ResponseBus)
        mock_response_bus.register_channel_adapter = AsyncMock()
        mock_agent = MagicMock()
        mock_agent.get_response_bus = AsyncMock(return_value=mock_response_bus)

        result = await adapter.initialize(agent=mock_agent)

        assert result is True
        assert adapter._initialized is True
