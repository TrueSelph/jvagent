"""Unit tests for ResponseBus incremental accumulation and finalization."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.response.message import ResponseMessage
from jvagent.action.response.response_bus import ResponseBus


class TestAppendToInteractionResponse:
    """Tests for _append_to_interaction_response_impl (instance-based)."""

    @pytest.mark.asyncio
    async def test_append_adhoc_first_message(self):
        """First adhoc message sets interaction.response."""
        bus = ResponseBus()
        mock_interaction = MagicMock()
        mock_interaction.response = None
        mock_interaction.set_response = MagicMock(return_value=True)
        mock_interaction._graph_context = MagicMock()
        mock_interaction.save = AsyncMock()

        await bus._append_to_interaction_response_impl(
            interaction=mock_interaction,
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
        mock_interaction.save = AsyncMock()

        await bus._append_to_interaction_response_impl(
            interaction=mock_interaction,
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
        mock_interaction.save = AsyncMock()

        await bus._append_to_interaction_response_impl(
            interaction=mock_interaction,
            message_type="stream_chunk",
            content="world!",
        )

        mock_interaction.set_response.assert_called_once_with("Hello world!")
        mock_interaction.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_append_no_save_when_unchanged(self):
        """When set_response returns False (unchanged), save is not called."""
        bus = ResponseBus()
        mock_interaction = MagicMock()
        mock_interaction.response = "Hello"
        mock_interaction.set_response = MagicMock(return_value=False)
        mock_interaction._graph_context = MagicMock()
        mock_interaction.save = AsyncMock()

        await bus._append_to_interaction_response_impl(
            interaction=mock_interaction,
            message_type="adhoc",
            content="Hello",
        )

        mock_interaction.save.assert_not_called()


class TestFinalizeInteractionSimplified:
    """Tests for simplified finalize_interaction (no response reconstruction)."""

    @pytest.mark.asyncio
    async def test_finalize_clears_buffers(self):
        """finalize_interaction clears message buffers."""
        bus = ResponseBus()
        bus._message_buffers["i1"] = [
            ResponseMessage(
                session_id="s1",
                user_id="u1",
                content="test",
                channel="default",
            )
        ]
        bus._buffer_timestamps["i1"] = 0.0

        mock_interaction = MagicMock()
        mock_interaction.response = "Already set"
        mock_interaction.observability_metrics = []
        mock_interaction.user_id = "test_user"

        await bus.finalize_interaction(
            interaction_id="i1",
            interaction=mock_interaction,
            session_id="s1",
            channel="default",
        )

        assert "i1" not in bus._message_buffers

    @pytest.mark.asyncio
    async def test_finalize_does_not_set_response(self):
        """finalize_interaction does not call interaction.set_response (response already accumulated)."""
        bus = ResponseBus()
        bus._message_buffers["i1"] = []
        mock_interaction = MagicMock()
        mock_interaction.response = "Accumulated during publish"
        mock_interaction.set_response = MagicMock()
        mock_interaction.user_id = "test_user"

        await bus.finalize_interaction(
            interaction_id="i1",
            interaction=mock_interaction,
            session_id="s1",
            channel="default",
        )

        mock_interaction.set_response.assert_not_called()

    @pytest.mark.asyncio
    async def test_finalize_emits_final_signal(self):
        """finalize_interaction calls _emit_final_signal for end-of-cycle."""
        bus = ResponseBus()
        bus._message_buffers["i1"] = []

        mock_interaction = MagicMock()
        mock_interaction.response = "Full response"
        mock_interaction.observability_metrics = []
        mock_interaction.user_id = "test_user"

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


class TestPublishIncrementalAccumulation:
    """Tests that publish triggers incremental accumulation when interaction_id/interaction present."""

    @pytest.mark.asyncio
    async def test_publish_stream_false_calls_append(self):
        """publish(stream=False) with interaction calls _append_to_interaction_response_impl."""
        bus = ResponseBus()
        bus._append_to_interaction_response_impl = AsyncMock()
        mock_interaction = MagicMock()
        mock_interaction.response = None

        await bus.publish(
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
    async def test_publish_stream_true_chunk_does_not_append(self):
        """publish(stream=True, streaming_complete=False) does not append until flush."""
        bus = ResponseBus()
        bus._append_to_interaction_response_impl = AsyncMock()

        await bus.publish(
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
    async def test_publish_no_interaction_id_skips_append(self):
        """publish(stream=False) without interaction_id/interaction does not call append."""
        bus = ResponseBus()
        bus._append_to_interaction_response_impl = AsyncMock()

        await bus.publish(
            session_id="s1",
            content="Reply",
            channel="default",
            stream=False,
            interaction_id=None,
            interaction=None,
            user_id="u1",
            streaming_complete=True,
        )

        bus._append_to_interaction_response_impl.assert_not_called()


class TestSimulatedStreaming:
    """Tests for simulated streaming (auto-detect and stream=False respect)."""

    @pytest.mark.asyncio
    async def test_stream_false_respected_with_subscribers(self):
        """When stream=False and subscribers exist, content is NOT chunked (respects explicit choice)."""
        bus = ResponseBus()
        mock_interaction = MagicMock()
        mock_interaction.response = None
        mock_interaction.set_response = MagicMock(return_value=True)
        mock_interaction._graph_context = MagicMock()
        mock_interaction.save = AsyncMock()

        # Add a subscriber (but should still respect stream=False)
        async def callback(msg):
            pass

        await bus.subscribe("s1", callback, receive_chunks=True)

        # Publish whole content with stream=False (explicit choice)
        content = "This is a complete message that should NOT be chunked"
        await bus.publish(
            session_id="s1",
            content=content,
            channel="default",
            stream=False,
            interaction_id="i1",
            interaction=mock_interaction,
            user_id="u1",
        )

        # Should have single adhoc message, no chunks
        messages = bus._message_buffers.get("i1", [])
        chunk_messages = [m for m in messages if m.message_type == "stream_chunk"]
        assert len(chunk_messages) == 0, "Should not auto-simulate chunks"

        # Should have one adhoc message
        adhoc_messages = [m for m in messages if m.message_type == "adhoc"]
        assert len(adhoc_messages) == 1

    @pytest.mark.asyncio
    async def test_no_simulated_streaming_without_subscribers(self):
        """When stream=False and no subscribers, content is NOT chunked."""
        bus = ResponseBus()
        mock_interaction = MagicMock()
        mock_interaction.response = None
        mock_interaction.set_response = MagicMock(return_value=True)
        mock_interaction._graph_context = MagicMock()
        mock_interaction.save = AsyncMock()

        # No subscribers
        content = "This is a complete message"
        await bus.publish(
            session_id="s1",
            content=content,
            channel="default",
            stream=False,
            interaction_id="i1",
            interaction=mock_interaction,
            user_id="u1",
        )

        # Should have single adhoc message, no chunks
        messages = bus._message_buffers.get("i1", [])
        chunk_messages = [m for m in messages if m.message_type == "stream_chunk"]
        assert len(chunk_messages) == 0, "Should not have chunks"

        # Should have one adhoc message
        adhoc_messages = [m for m in messages if m.message_type == "adhoc"]
        assert len(adhoc_messages) == 1

    @pytest.mark.asyncio
    async def test_short_content_not_chunked(self):
        """With stream=False, content is delivered as single adhoc (no simulated streaming)."""
        bus = ResponseBus()
        mock_interaction = MagicMock()
        mock_interaction.response = None
        mock_interaction.set_response = MagicMock(return_value=True)
        mock_interaction._graph_context = MagicMock()
        mock_interaction.save = AsyncMock()

        # Add subscriber
        async def callback(msg):
            pass

        await bus.subscribe("s1", callback, receive_chunks=True)

        # Short content
        content = "Short"
        await bus.publish(
            session_id="s1",
            content=content,
            channel="default",
            stream=False,
            interaction_id="i1",
            interaction=mock_interaction,
            user_id="u1",
        )

        # stream=False: single adhoc, no chunks
        messages = bus._message_buffers.get("i1", [])
        chunk_messages = [m for m in messages if m.message_type == "stream_chunk"]
        assert len(chunk_messages) == 0

    @pytest.mark.asyncio
    async def test_auto_detect_whole_content_with_stream_true(self):
        """When stream=True and streaming_complete=True with non-empty content, auto-simulate streaming."""
        bus = ResponseBus()
        mock_interaction = MagicMock()
        mock_interaction.response = None
        mock_interaction.set_response = MagicMock(return_value=True)
        mock_interaction._graph_context = MagicMock()
        mock_interaction.save = AsyncMock()

        # One call with stream=True, streaming_complete=True, full content (whole content in one shot)
        content = "This is a complete message that should be chunked for streaming"
        result = await bus.publish(
            session_id="s1",
            content=content,
            channel="default",
            stream=True,
            interaction_id="i1",
            interaction=mock_interaction,
            user_id="u1",
            streaming_complete=True,
        )

        # Should return final_message (message_type="final")
        assert result.message_type == "final"

        # Should have multiple stream_chunk messages plus adhoc and final
        messages = bus._message_buffers.get("i1", [])
        chunk_messages = [m for m in messages if m.message_type == "stream_chunk"]
        assert (
            len(chunk_messages) > 1
        ), "Should auto-detect and simulate multiple chunks"
        assert "".join(m.content for m in chunk_messages) == content

    @pytest.mark.asyncio
    async def test_thought_stream_adhoc_flush_reuses_accumulator_message_id(self):
        """Flush adhoc must use the same id as stream_chunk rows so clients merge one bubble."""
        bus = ResponseBus()
        interaction = MagicMock()
        interaction.append_agent_trace = MagicMock(return_value=True)
        interaction._graph_context = MagicMock()
        interaction.save = AsyncMock()
        seg = "iter-1-reasoning"

        await bus.publish(
            session_id="s1",
            content="a",
            channel="default",
            stream=True,
            interaction_id="i1",
            interaction=interaction,
            user_id="u1",
            streaming_complete=False,
            category="thought",
            thought_type="reasoning",
            segment_id=seg,
        )
        await bus.publish(
            session_id="s1",
            content="b",
            channel="default",
            stream=True,
            interaction_id="i1",
            interaction=interaction,
            user_id="u1",
            streaming_complete=True,
            category="thought",
            thought_type="reasoning",
            segment_id=seg,
        )

        messages = bus._message_buffers.get("i1", [])
        chunks = [
            m
            for m in messages
            if m.message_type == "stream_chunk" and m.category == "thought"
        ]
        flushes = [
            m for m in messages if m.message_type == "adhoc" and m.category == "thought"
        ]
        assert len(chunks) == 2
        assert len(flushes) == 1
        assert chunks[0].id
        assert chunks[0].id == flushes[0].id


class TestThoughtMessageRouting:
    @pytest.mark.asyncio
    async def test_publish_thought_appends_agent_trace_not_response(self):
        bus = ResponseBus()
        interaction = MagicMock()
        interaction.response = None
        interaction.set_response = MagicMock(return_value=True)
        interaction.append_agent_trace = MagicMock(return_value=True)
        interaction._graph_context = MagicMock()
        interaction.save = AsyncMock()

        await bus.publish(
            session_id="s1",
            content="model is thinking",
            channel="default",
            stream=False,
            interaction_id="i1",
            interaction=interaction,
            user_id="u1",
            category="thought",
            thought_type="reasoning",
            segment_id="iter-1-reasoning",
        )

        interaction.set_response.assert_not_called()
        interaction.append_agent_trace.assert_called_once()

    @pytest.mark.asyncio
    async def test_thought_adapter_relay_requires_opt_in(self):
        bus = ResponseBus()
        adapter = MagicMock()
        adapter.channel = "default"
        adapter.send = AsyncMock(return_value=True)
        adapter.deliver_thoughts = False
        bus._channel_adapters["default"] = adapter

        await bus.publish(
            session_id="s1",
            content="thinking",
            channel="default",
            stream=False,
            interaction_id="i1",
            interaction=None,
            user_id="u1",
            category="thought",
            thought_type="reasoning",
            segment_id="seg-1",
            relay_to_adapters=True,
        )
        adapter.send.assert_not_called()

        adapter.deliver_thoughts = True
        await bus.publish(
            session_id="s1",
            content="thinking again",
            channel="default",
            stream=False,
            interaction_id="i1",
            interaction=None,
            user_id="u1",
            category="thought",
            thought_type="reasoning",
            segment_id="seg-2",
            relay_to_adapters=True,
        )
        adapter.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_and_thought_accumulators_are_isolated(self):
        bus = ResponseBus()
        interaction = MagicMock()
        interaction.response = None
        interaction.set_response = MagicMock(return_value=True)
        interaction.append_agent_trace = MagicMock(return_value=True)
        interaction._graph_context = MagicMock()
        interaction.save = AsyncMock()

        await bus.publish(
            session_id="s1",
            content="Hello ",
            channel="default",
            stream=True,
            interaction_id="i1",
            interaction=interaction,
            user_id="u1",
            streaming_complete=False,
            category="user",
        )
        await bus.publish(
            session_id="s1",
            content="inspect tool call",
            channel="default",
            stream=True,
            interaction_id="i1",
            interaction=interaction,
            user_id="u1",
            streaming_complete=False,
            category="thought",
            thought_type="tool_call",
            segment_id="iter-1-call-read_file-0",
        )

        assert "i1" in bus._adhoc_accumulation
        assert ("i1", "iter-1-call-read_file-0") in bus._thought_accumulation

        await bus.commit_pending_adhoc("i1", interaction)
        await bus.commit_pending_thoughts("i1", interaction)

        assert "i1" not in bus._adhoc_accumulation
        assert ("i1", "iter-1-call-read_file-0") not in bus._thought_accumulation
        interaction.set_response.assert_called()
        interaction.append_agent_trace.assert_called()


class TestThoughtPublishNormalization:
    """Thought bodies are normalized on flush (whitespace only)."""

    @pytest.mark.asyncio
    async def test_publish_thought_normalizes_before_agent_trace(self):
        bus = ResponseBus()
        interaction = MagicMock()
        interaction.response = None
        interaction.set_response = MagicMock(return_value=True)
        interaction.append_agent_trace = MagicMock(return_value=True)
        interaction._graph_context = MagicMock()
        interaction.save = AsyncMock()

        await bus.publish(
            session_id="s1",
            content="a\n\n\n\nb",
            channel="default",
            stream=False,
            interaction_id="i1",
            interaction=interaction,
            user_id="u1",
            category="thought",
            thought_type="reasoning",
            segment_id="seg-norm",
        )

        interaction.append_agent_trace.assert_called_once()
        entry = interaction.append_agent_trace.call_args[0][0]
        assert entry["content"] == "a\n\nb"
