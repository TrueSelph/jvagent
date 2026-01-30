"""Unit tests for ResponseBus incremental accumulation and finalization."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from jvagent.action.response.message import ResponseMessage
from jvagent.action.response.response_bus import ResponseBus


class TestAppendToInteractionResponse:
    """Tests for _append_to_interaction_response (incremental state management)."""

    @pytest.mark.asyncio
    async def test_append_adhoc_first_message(self):
        """First adhoc message sets interaction.response."""
        bus = ResponseBus()
        mock_interaction = MagicMock()
        mock_interaction.response = None
        mock_interaction.set_response = MagicMock(return_value=True)
        mock_interaction._graph_context = None

        mock_context = MagicMock()
        mock_context.get = AsyncMock(return_value=mock_interaction)

        with patch("jvagent.action.response.response_bus.get_prime_database") as mock_db:
            with patch("jvagent.action.response.response_bus.GraphContext", return_value=mock_context):
                await bus._append_to_interaction_response(
                    interaction_id="n.Interaction.test123",
                    message_type="adhoc",
                    content="Hello",
                )

        mock_interaction.set_response.assert_called_once_with("Hello")
        mock_interaction.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_append_adhoc_second_message(self):
        """Second adhoc message appends with double newline."""
        bus = ResponseBus()
        mock_interaction = MagicMock()
        mock_interaction.response = "First message"
        mock_interaction.set_response = MagicMock(return_value=True)
        mock_interaction._graph_context = MagicMock()

        mock_context = MagicMock()
        mock_context.get = AsyncMock(return_value=mock_interaction)

        with patch("jvagent.action.response.response_bus.get_prime_database"):
            with patch("jvagent.action.response.response_bus.GraphContext", return_value=mock_context):
                await bus._append_to_interaction_response(
                    interaction_id="n.Interaction.test123",
                    message_type="adhoc",
                    content="Second message",
                )

        mock_interaction.set_response.assert_called_once_with(
            "First message\n\nSecond message"
        )
        mock_interaction.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_append_stream_chunk_concatenates(self):
        """Stream chunks concatenate without separators."""
        bus = ResponseBus()
        mock_interaction = MagicMock()
        mock_interaction.response = "Hello "
        mock_interaction.set_response = MagicMock(return_value=True)
        mock_interaction._graph_context = MagicMock()

        mock_context = MagicMock()
        mock_context.get = AsyncMock(return_value=mock_interaction)

        with patch("jvagent.action.response.response_bus.get_prime_database"):
            with patch("jvagent.action.response.response_bus.GraphContext", return_value=mock_context):
                await bus._append_to_interaction_response(
                    interaction_id="n.Interaction.test123",
                    message_type="stream_chunk",
                    content="world!",
                )

        mock_interaction.set_response.assert_called_once_with("Hello world!")
        mock_interaction.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_append_no_interaction_skips_gracefully(self):
        """Missing interaction does not raise."""
        bus = ResponseBus()
        mock_context = MagicMock()
        mock_context.get = AsyncMock(return_value=None)

        with patch("jvagent.action.response.response_bus.get_prime_database"):
            with patch("jvagent.action.response.response_bus.GraphContext", return_value=mock_context):
                await bus._append_to_interaction_response(
                    interaction_id="n.Interaction.missing",
                    message_type="adhoc",
                    content="Hello",
                )
        # No exception, no save

    @pytest.mark.asyncio
    async def test_append_no_save_when_unchanged(self):
        """When set_response returns False (unchanged), save is not called."""
        bus = ResponseBus()
        mock_interaction = MagicMock()
        mock_interaction.response = "Hello"
        mock_interaction.set_response = MagicMock(return_value=False)
        mock_interaction._graph_context = MagicMock()

        mock_context = MagicMock()
        mock_context.get = AsyncMock(return_value=mock_interaction)

        with patch("jvagent.action.response.response_bus.get_prime_database"):
            with patch("jvagent.action.response.response_bus.GraphContext", return_value=mock_context):
                await bus._append_to_interaction_response(
                    interaction_id="n.Interaction.test123",
                    message_type="adhoc",
                    content="Hello",
                )

        mock_interaction.save.assert_not_called()


class TestFinalizeInteractionSimplified:
    """Tests for simplified finalize_interaction (no response reconstruction)."""

    @pytest.mark.asyncio
    async def test_finalize_clears_buffers(self):
        """finalize_interaction clears message and observability buffers."""
        bus = ResponseBus()
        bus._message_buffers["i1"] = [MagicMock()]
        bus._observability_buffers["i1"] = []
        bus._buffer_timestamps["i1"] = 0.0

        mock_interaction = MagicMock()
        mock_interaction.response = "Already set"
        mock_interaction.observability_metrics = None

        await bus.finalize_interaction(
            interaction_id="i1",
            interaction=mock_interaction,
            session_id="s1",
            channel="default",
        )

        assert "i1" not in bus._message_buffers
        assert "i1" not in bus._observability_buffers
        assert "i1" not in bus._buffer_timestamps

    @pytest.mark.asyncio
    async def test_finalize_does_not_set_response(self):
        """finalize_interaction does not call interaction.set_response (response already accumulated)."""
        bus = ResponseBus()
        bus._message_buffers["i1"] = []
        mock_interaction = MagicMock()
        mock_interaction.response = "Accumulated during publish"
        mock_interaction.set_response = MagicMock()

        await bus.finalize_interaction(
            interaction_id="i1",
            interaction=mock_interaction,
            session_id="s1",
            channel="default",
        )

        mock_interaction.set_response.assert_not_called()

    @pytest.mark.asyncio
    async def test_finalize_attaches_observability_when_changed(self):
        """finalize_interaction attaches observability_metrics when present and changed."""
        bus = ResponseBus()
        bus._message_buffers["i1"] = []
        bus._observability_buffers["i1"] = [{"event_type": "model_call", "data": {}}]
        bus._buffer_timestamps["i1"] = 0.0

        mock_interaction = MagicMock()
        mock_interaction.response = ""
        mock_interaction.observability_metrics = None
        mock_interaction.save = AsyncMock()

        await bus.finalize_interaction(
            interaction_id="i1",
            interaction=mock_interaction,
            session_id="s1",
            channel="default",
        )

        assert mock_interaction.observability_metrics == [
            {"event_type": "model_call", "data": {}}
        ]
        mock_interaction.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_finalize_emits_final_signal(self):
        """finalize_interaction calls _emit_final_signal for end-of-cycle."""
        bus = ResponseBus()
        bus._message_buffers["i1"] = []
        bus._observability_buffers["i1"] = []
        bus._buffer_timestamps["i1"] = 0.0

        mock_interaction = MagicMock()
        mock_interaction.response = "Full response"
        mock_interaction.observability_metrics = None

        bus._emit_final_signal = AsyncMock()

        await bus.finalize_interaction(
            interaction_id="i1",
            interaction=mock_interaction,
            session_id="s1",
            channel="default",
        )

        bus._emit_final_signal.assert_called_once()
        call_kw = bus._emit_final_signal.call_args[1]
        assert call_kw["session_id"] == "s1"
        assert call_kw["channel"] == "default"
        assert call_kw["interaction_id"] == "i1"


class TestPublishAdhocIncrementalAccumulation:
    """Tests that publish_adhoc triggers incremental accumulation when interaction_id/interaction present."""

    @pytest.mark.asyncio
    async def test_publish_adhoc_stream_false_calls_append(self):
        """publish_adhoc(stream=False) with interaction calls _append_to_interaction_response_impl."""
        bus = ResponseBus()
        bus._append_to_interaction_response_impl = AsyncMock()
        mock_interaction = MagicMock()
        mock_interaction.response = None

        await bus.publish_adhoc(
            session_id="s1",
            content="Reply",
            channel="default",
            stream=False,
            interaction_id="n.Interaction.xyz",
            interaction=mock_interaction,
            user_id="u1",
            streaming_complete=True,
        )

        bus._append_to_interaction_response_impl.assert_called_once()
        call_kw = bus._append_to_interaction_response_impl.call_args[1]
        assert call_kw["message_type"] == "adhoc"
        assert call_kw["content"] == "Reply"

    @pytest.mark.asyncio
    async def test_publish_adhoc_stream_true_chunk_does_not_append(self):
        """publish_adhoc(stream=True, streaming_complete=False) does not append until flush."""
        bus = ResponseBus()
        bus._append_to_interaction_response_impl = AsyncMock()

        await bus.publish_adhoc(
            session_id="s1",
            content="chunk",
            channel="default",
            stream=True,
            interaction_id="n.Interaction.xyz",
            interaction=None,
            user_id="u1",
            streaming_complete=False,
        )

        bus._append_to_interaction_response_impl.assert_not_called()

    @pytest.mark.asyncio
    async def test_publish_adhoc_no_interaction_id_skips_append(self):
        """publish_adhoc(stream=False) without interaction_id/interaction does not call append."""
        bus = ResponseBus()
        bus._append_to_interaction_response = AsyncMock()
        bus._append_to_interaction_response_impl = AsyncMock()

        await bus.publish_adhoc(
            session_id="s1",
            content="Reply",
            channel="default",
            stream=False,
            interaction_id=None,
            interaction=None,
            user_id="u1",
            streaming_complete=True,
        )

        bus._append_to_interaction_response.assert_not_called()
        bus._append_to_interaction_response_impl.assert_not_called()
