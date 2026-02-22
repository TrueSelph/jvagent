"""Unit tests for ChannelAdapter."""

from unittest.mock import AsyncMock, MagicMock, patch

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
    async def test_initialize_returns_false_when_app_unavailable(self):
        """initialize() returns False when App.get() returns None."""
        adapter = StubChannelAdapter(channel="test")

        with patch("jvagent.core.app.App") as mock_app_class:
            mock_app_class.get = AsyncMock(return_value=None)

            result = await adapter.initialize()

            assert result is False
            assert adapter._initialized is False

    @pytest.mark.asyncio
    async def test_initialize_returns_false_when_response_bus_unavailable(self):
        """initialize() returns False when ResponseBus is not available."""
        adapter = StubChannelAdapter(channel="test")
        mock_app = MagicMock()
        mock_app.get_response_bus = AsyncMock(return_value=None)

        with patch("jvagent.core.app.App") as mock_app_class:
            mock_app_class.get = AsyncMock(return_value=mock_app)

            result = await adapter.initialize()

            assert result is False
            assert adapter._initialized is False

    @pytest.mark.asyncio
    async def test_initialize_returns_true_when_success(self):
        """initialize() returns True when App and ResponseBus are available."""
        adapter = StubChannelAdapter(channel="test")
        mock_response_bus = MagicMock(spec=ResponseBus)
        mock_response_bus.register_channel_adapter = AsyncMock()
        mock_app = MagicMock()
        mock_app.get_response_bus = AsyncMock(return_value=mock_response_bus)

        with patch("jvagent.core.app.App") as mock_app_class:
            mock_app_class.get = AsyncMock(return_value=mock_app)

            result = await adapter.initialize()

            assert result is True
            assert adapter._initialized is True
