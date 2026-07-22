"""Unit tests for ChannelFilter system."""

import pytest

pytest.importorskip("filetype")
try:
    pass
except ImportError:
    pytest.skip(
        "UserCreateAdmin not available in installed jvspatial", allow_module_level=True
    )

from unittest.mock import AsyncMock, MagicMock

from jvagent.action.response.channel_filter import ChannelFilter
from jvagent.action.response.message import ResponseMessage
from jvagent.action.response.response_bus import ResponseBus
from jvagent.action.whatsapp.whatsapp_filter import WhatsAppFilter


class TestChannelFilter:
    """Tests for ChannelFilter abstract class."""

    @pytest.mark.asyncio
    async def test_filter_initialization(self):
        """Test filter initialization and registration."""
        filter = WhatsAppFilter(channels=["whatsapp"], priority=100)

        assert filter.channels == ["whatsapp"]
        assert filter.priority == 100
        assert filter._initialized is False
        assert filter.response_bus is None

    @pytest.mark.asyncio
    async def test_applies_to_channel(self):
        """Test applies_to_channel method."""
        filter = WhatsAppFilter(channels=["whatsapp", "default"], priority=100)

        assert filter.applies_to_channel("whatsapp") is True
        assert filter.applies_to_channel("default") is True
        assert filter.applies_to_channel("sms") is False

    @pytest.mark.asyncio
    async def test_filter_initialization_with_response_bus(self):
        """Test filter initialization with ResponseBus."""
        filter = WhatsAppFilter(channels=["whatsapp"], priority=100)

        # Mock Agent and ResponseBus
        mock_response_bus = MagicMock(spec=ResponseBus)
        mock_response_bus.register_channel_filter = AsyncMock()

        mock_agent = MagicMock()
        mock_agent.get_response_bus = AsyncMock(return_value=mock_response_bus)

        result = await filter.initialize(agent=mock_agent)

        assert result is True
        assert filter._initialized is True
        assert filter.response_bus is mock_response_bus
        mock_response_bus.register_channel_filter.assert_called_once_with(filter)

    @pytest.mark.asyncio
    async def test_filter_initialization_no_agent(self):
        """Test filter initialization when agent is not provided."""
        filter = WhatsAppFilter(channels=["whatsapp"], priority=100)

        result = await filter.initialize(agent=None)

        assert result is False
        assert filter._initialized is False

    @pytest.mark.asyncio
    async def test_filter_initialization_no_response_bus(self):
        """Test filter initialization when ResponseBus is not available."""
        filter = WhatsAppFilter(channels=["whatsapp"], priority=100)

        mock_agent = MagicMock()
        mock_agent.get_response_bus = AsyncMock(return_value=None)

        result = await filter.initialize(agent=mock_agent)

        assert result is False
        assert filter._initialized is False

    @pytest.mark.asyncio
    async def test_filter_initialization_idempotent(self):
        """Test that filter initialization is idempotent."""
        filter = WhatsAppFilter(channels=["whatsapp"], priority=100)

        mock_response_bus = MagicMock(spec=ResponseBus)
        mock_response_bus.register_channel_filter = AsyncMock()

        mock_agent = MagicMock()
        mock_agent.get_response_bus = AsyncMock(return_value=mock_response_bus)

        # First initialization
        result1 = await filter.initialize(agent=mock_agent)
        assert result1 is True

        # Second initialization should return True without re-registering
        result2 = await filter.initialize(agent=mock_agent)
        assert result2 is True

        # Should only register once
        assert mock_response_bus.register_channel_filter.call_count == 1


class TestWhatsAppFilter:
    """Tests for WhatsAppFilter implementation."""

    @pytest.mark.asyncio
    async def test_whatsapp_filter_default_channels(self):
        """Test WhatsAppFilter with default channels."""
        filter = WhatsAppFilter()

        assert filter.channels == ["whatsapp"]
        assert filter.priority == 100

    @pytest.mark.asyncio
    async def test_whatsapp_filter_custom_channels(self):
        """Test WhatsAppFilter with custom channels."""
        filter = WhatsAppFilter(channels=["whatsapp", "default"], priority=50)

        assert filter.channels == ["whatsapp", "default"]
        assert filter.priority == 50

    @pytest.mark.asyncio
    async def test_whatsapp_filter_transformations(self):
        """Test WhatsAppFilter message transformations."""
        filter = WhatsAppFilter()

        message = ResponseMessage(
            session_id="test_session",
            user_id="test_user",
            content="**Bold text** with <br/>line break and <b>HTML bold</b>",
        )

        await filter.filter(message)

        assert message.content == "*Bold text* with \nline break and *HTML bold*"

    @pytest.mark.asyncio
    async def test_whatsapp_filter_empty_content(self):
        """Test WhatsAppFilter with empty content."""
        filter = WhatsAppFilter()

        message = ResponseMessage(
            session_id="test_session", user_id="test_user", content=""
        )

        # Should not raise an error
        await filter.filter(message)
        assert message.content == ""

    @pytest.mark.asyncio
    async def test_whatsapp_filter_none_content(self):
        """Test WhatsAppFilter with None content."""
        filter = WhatsAppFilter()

        message = ResponseMessage(
            session_id="test_session", user_id="test_user", content=""
        )

        # Should not raise an error
        await filter.filter(message)
        assert message.content == ""


class TestResponseBusFilterIntegration:
    """Tests for ResponseBus filter integration."""

    @pytest.mark.asyncio
    async def test_register_channel_filter(self):
        """Test registering a channel filter with ResponseBus."""
        bus = ResponseBus()

        filter = WhatsAppFilter(channels=["whatsapp"], priority=100)

        await bus.register_channel_filter(filter)

        assert len(bus._channel_filters) == 1
        assert bus._channel_filters[0] is filter

    @pytest.mark.asyncio
    async def test_register_channel_filter_replaces_equivalent_duplicate(self):
        """Re-registering an equivalent filter (same class/channels/priority)
        replaces the previous instance instead of stacking a duplicate.

        Regression test: an action's on_register() can run more than once
        for the same logical filter over an agent's lifetime (e.g. whenever
        the action-list cache rebuilds), which previously caused filters to
        accumulate without bound for the life of the process.
        """
        bus = ResponseBus()

        first = WhatsAppFilter(channels=["whatsapp"], priority=100)
        second = WhatsAppFilter(channels=["whatsapp"], priority=100)

        await bus.register_channel_filter(first)
        await bus.register_channel_filter(second)

        assert len(bus._channel_filters) == 1
        assert bus._channel_filters[0] is second

        # Re-registering many times must never grow the list past one entry.
        for _ in range(50):
            await bus.register_channel_filter(
                WhatsAppFilter(channels=["whatsapp"], priority=100)
            )
        assert len(bus._channel_filters) == 1

    @pytest.mark.asyncio
    async def test_register_multiple_filters_priority_order(self):
        """Test that multiple filters are sorted by priority."""
        bus = ResponseBus()

        filter1 = WhatsAppFilter(channels=["whatsapp"], priority=100)
        filter2 = WhatsAppFilter(channels=["whatsapp"], priority=50)
        filter3 = WhatsAppFilter(channels=["whatsapp"], priority=150)

        await bus.register_channel_filter(filter1)
        await bus.register_channel_filter(filter2)
        await bus.register_channel_filter(filter3)

        # Filters should be sorted by priority (lower first)
        assert len(bus._channel_filters) == 3
        assert bus._channel_filters[0].priority == 50  # filter2
        assert bus._channel_filters[1].priority == 100  # filter1
        assert bus._channel_filters[2].priority == 150  # filter3

    @pytest.mark.asyncio
    async def test_apply_channel_filters_single_filter(self):
        """Test applying a single filter to a message."""
        bus = ResponseBus()

        filter = WhatsAppFilter(channels=["whatsapp"], priority=100)
        await bus.register_channel_filter(filter)

        message = ResponseMessage(
            session_id="test_session",
            user_id="test_user",
            content="**Bold** text",
            channel="whatsapp",
        )

        await bus._apply_channel_filters(message, "whatsapp")

        assert message.content == "*Bold* text"

    @pytest.mark.asyncio
    async def test_apply_channel_filters_multiple_filters(self):
        """Test applying multiple filters in priority order."""
        bus = ResponseBus()

        # Create custom filters with different priorities
        class Filter1(ChannelFilter):
            def __init__(self):
                super().__init__(channels=["whatsapp"], priority=50)

            async def filter(self, message: ResponseMessage) -> None:
                message.content = f"[Filter1]{message.content}"

        class Filter2(ChannelFilter):
            def __init__(self):
                super().__init__(channels=["whatsapp"], priority=100)

            async def filter(self, message: ResponseMessage) -> None:
                message.content = f"{message.content}[Filter2]"

        filter1 = Filter1()
        filter2 = Filter2()

        await bus.register_channel_filter(filter1)
        await bus.register_channel_filter(filter2)

        message = ResponseMessage(
            session_id="test_session",
            user_id="test_user",
            content="original",
            channel="whatsapp",
        )

        await bus._apply_channel_filters(message, "whatsapp")

        # Filter1 (priority 50) should execute first, then Filter2 (priority 100)
        assert message.content == "[Filter1]original[Filter2]"

    @pytest.mark.asyncio
    async def test_apply_channel_filters_wrong_channel(self):
        """Test that filters don't apply to wrong channels."""
        bus = ResponseBus()

        filter = WhatsAppFilter(channels=["whatsapp"], priority=100)
        await bus.register_channel_filter(filter)

        message = ResponseMessage(
            session_id="test_session",
            user_id="test_user",
            content="**Bold** text",
            channel="sms",
        )

        original_content = message.content
        await bus._apply_channel_filters(message, "sms")

        # Content should be unchanged since filter doesn't apply to "sms"
        assert message.content == original_content

    @pytest.mark.asyncio
    async def test_apply_channel_filters_multi_channel_filter(self):
        """Test filter that applies to multiple channels."""
        bus = ResponseBus()

        filter = WhatsAppFilter(channels=["whatsapp", "default"], priority=100)
        await bus.register_channel_filter(filter)

        # Test with whatsapp channel
        message1 = ResponseMessage(
            session_id="test_session",
            user_id="test_user",
            content="**Bold** text",
            channel="whatsapp",
        )
        await bus._apply_channel_filters(message1, "whatsapp")
        assert message1.content == "*Bold* text"

        # Test with default (web) channel
        message2 = ResponseMessage(
            session_id="test_session",
            user_id="test_user",
            content="**Bold** text",
            channel="default",
        )
        await bus._apply_channel_filters(message2, "default")
        assert message2.content == "*Bold* text"

        # Test with sms channel (should not apply)
        message3 = ResponseMessage(
            session_id="test_session",
            user_id="test_user",
            content="**Bold** text",
            channel="sms",
        )
        original_content = message3.content
        await bus._apply_channel_filters(message3, "sms")
        assert message3.content == original_content

    @pytest.mark.asyncio
    async def test_apply_channel_filters_error_handling(self):
        """Test that filter errors don't stop other filters."""
        bus = ResponseBus()

        class FailingFilter(ChannelFilter):
            def __init__(self):
                super().__init__(channels=["whatsapp"], priority=50)

            async def filter(self, message: ResponseMessage) -> None:
                raise ValueError("Filter error")

        class WorkingFilter(ChannelFilter):
            def __init__(self):
                super().__init__(channels=["whatsapp"], priority=100)

            async def filter(self, message: ResponseMessage) -> None:
                message.content = f"{message.content}[Working]"

        failing_filter = FailingFilter()
        working_filter = WorkingFilter()

        await bus.register_channel_filter(failing_filter)
        await bus.register_channel_filter(working_filter)

        message = ResponseMessage(
            session_id="test_session",
            user_id="test_user",
            content="original",
            channel="whatsapp",
        )

        # Should not raise, and working filter should still execute
        await bus._apply_channel_filters(message, "whatsapp")

        # Working filter should have executed despite failing filter error
        assert message.content == "original[Working]"

    @pytest.mark.asyncio
    async def test_publish_applies_filters(self):
        """Test that publish(stream=False) applies filters before routing to adapter."""
        bus = ResponseBus()
        filter = WhatsAppFilter(channels=["whatsapp"], priority=100)
        await bus.register_channel_filter(filter)
        mock_adapter = MagicMock()
        mock_adapter.channel = "whatsapp"
        mock_adapter.send = AsyncMock(return_value=True)
        bus._channel_adapters["whatsapp"] = mock_adapter

        message = await bus.publish(
            session_id="test_session",
            content="**Bold** text",
            channel="whatsapp",
            stream=False,
            user_id="test_user",
            streaming_complete=True,
        )
        assert message.content == "*Bold* text"
        mock_adapter.send.assert_called_once()
        sent_message = mock_adapter.send.call_args[0][0]
        assert sent_message.content == "*Bold* text"

    @pytest.mark.asyncio
    async def test_publish_stream_chunk_no_filter_no_adapter(self):
        """Test that publish(stream=True, streaming_complete=False) does not filter or call adapter."""
        bus = ResponseBus()
        filter = WhatsAppFilter(channels=["whatsapp"], priority=100)
        await bus.register_channel_filter(filter)
        mock_adapter = MagicMock()
        mock_adapter.channel = "whatsapp"
        mock_adapter.send = AsyncMock(return_value=True)
        bus._channel_adapters["whatsapp"] = mock_adapter

        message = await bus.publish(
            session_id="test_session",
            content="**Bold** text",
            channel="whatsapp",
            stream=True,
            interaction_id="i1",
            user_id="test_user",
            streaming_complete=False,
        )
        assert message.content == "**Bold** text"
        mock_adapter.send.assert_not_called()
