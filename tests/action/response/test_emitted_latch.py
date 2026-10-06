"""Bus delivery latches interaction.emitted for user content only."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from jvagent.action.response.channel_adapter import ChannelAdapter
from jvagent.action.response.message import ResponseMessage
from jvagent.action.response.response_bus import ChannelDeliveryError, ResponseBus
from jvagent.memory.interaction import Interaction


@pytest.mark.asyncio
async def test_user_publish_latches_emitted():
    bus = ResponseBus()
    interaction = Interaction()
    await bus.publish(
        session_id="s1",
        content="hello",
        channel="default",
        interaction=interaction,
        interaction_id="i1",
        category="user",
    )
    assert interaction.has_emitted() is True


@pytest.mark.asyncio
async def test_thought_publish_does_not_latch():
    bus = ResponseBus()
    interaction = Interaction()
    await bus.publish(
        session_id="s1",
        content="(thinking)",
        channel="default",
        interaction=interaction,
        interaction_id="i1",
        category="thought",
        thought_type="reasoning",
    )
    assert interaction.has_emitted() is False


@pytest.mark.asyncio
async def test_transient_user_publish_does_not_latch():
    bus = ResponseBus()
    interaction = Interaction()
    await bus.publish(
        session_id="s1",
        content="typing...",
        channel="default",
        interaction=interaction,
        interaction_id="i1",
        category="user",
        transient=True,
    )
    assert interaction.has_emitted() is False


@pytest.mark.asyncio
async def test_second_user_egress_is_suppressed_at_the_bus():
    bus = ResponseBus()
    interaction = Interaction()
    seen = []

    async def on_message(message):
        seen.append(message)

    await bus.subscribe("s1", on_message, receive_chunks=True)
    await bus.publish(
        session_id="s1",
        content="First answer.",
        channel="default",
        interaction=interaction,
        interaction_id=interaction.id,
        category="user",
    )
    await bus.publish(
        session_id="s1",
        content="A distinct second answer must not escape.",
        channel="default",
        interaction=interaction,
        interaction_id=interaction.id,
        category="user",
    )

    assert [message.content for message in seen] == ["First answer."]
    assert interaction.response == "First answer."


@pytest.mark.asyncio
async def test_replayed_delivery_can_keep_a_stable_message_identifier():
    bus = ResponseBus()
    stable_id = "o.ResponseMessage.pilot_0123456789abcdef01234567"
    first = await bus.publish(
        session_id="s1",
        content="The saved answer.",
        channel="default",
        interaction=Interaction(),
        interaction_id="interaction-first",
        category="user",
        message_id=stable_id,
    )
    replay = await bus.publish(
        session_id="s1",
        content="The saved answer.",
        channel="default",
        interaction=Interaction(),
        interaction_id="interaction-retry",
        category="user",
        message_id=stable_id,
    )

    assert first.id == stable_id
    assert replay.id == stable_id


@pytest.mark.asyncio
async def test_message_id_rejects_unbounded_or_unscoped_identifiers():
    bus = ResponseBus()
    with pytest.raises(ValueError, match="bounded ResponseMessage"):
        await bus.publish(
            session_id="s1",
            content="hello",
            channel="default",
            message_id="arbitrary-id",
        )


@pytest.mark.asyncio
async def test_required_adapter_rejection_fails_before_task_can_be_acknowledged():
    class RejectingAdapter(ChannelAdapter):
        async def send(self, message: ResponseMessage) -> bool:
            return False

    bus = ResponseBus()
    adapter = RejectingAdapter(channel="partner")
    adapter.send = AsyncMock(return_value=False)
    bus._channel_adapters["partner"] = adapter
    interaction = Interaction()
    await bus.subscribe("session-1", AsyncMock(), receive_chunks=True)

    with pytest.raises(ChannelDeliveryError, match="did not confirm delivery"):
        await bus.publish(
            session_id="session-1",
            content="Saved answer",
            channel="partner",
            interaction=interaction,
            interaction_id=interaction.id,
            category="user",
            message_id="o.ResponseMessage.pilot_0123456789abcdef01234567",
            require_adapter_ack=True,
        )

    adapter.send.assert_awaited_once()
    assert interaction.has_emitted() is False
    assert bus._session_queues.get("session-1", []) == []


@pytest.mark.asyncio
async def test_adapter_rejection_keeps_legacy_queue_behavior_without_required_ack():
    class RejectingAdapter(ChannelAdapter):
        async def send(self, message: ResponseMessage) -> bool:
            return False

    bus = ResponseBus()
    adapter = RejectingAdapter(channel="partner")
    adapter.send = AsyncMock(return_value=False)
    bus._channel_adapters["partner"] = adapter
    interaction = Interaction()

    message = await bus.publish(
        session_id="session-legacy",
        content="Existing adapter behavior",
        channel="partner",
        interaction=interaction,
        interaction_id=interaction.id,
        category="user",
    )

    assert message.content == "Existing adapter behavior"
    assert len(bus._session_queues["session-legacy"]) == 1


@pytest.mark.asyncio
async def test_adapter_exception_is_not_retried_without_idempotent_replay():
    class UnkeyedAdapter(ChannelAdapter):
        async def send(self, message: ResponseMessage) -> bool:
            raise TimeoutError("provider acknowledgment timed out")

    bus = ResponseBus()
    adapter = UnkeyedAdapter(channel="partner")
    adapter.send = AsyncMock(side_effect=TimeoutError("acknowledgment lost"))

    delivered = await bus._send_to_adapter(
        adapter, ResponseMessage(channel="partner", content="one message")
    )

    assert delivered is False
    adapter.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_adapter_exception_can_retry_when_idempotent_replay_is_declared():
    class KeyedAdapter(ChannelAdapter):
        async def send(self, message: ResponseMessage) -> bool:
            return True

    bus = ResponseBus()
    adapter = KeyedAdapter(channel="partner")
    adapter.supports_idempotent_replay = True
    adapter.send = AsyncMock(side_effect=[TimeoutError("acknowledgment lost"), True])
    message = ResponseMessage(channel="partner", content="one message")

    delivered = await bus._send_to_adapter(adapter, message)

    assert delivered is True
    assert adapter.send.await_count == 2
    assert [call.args[0].id for call in adapter.send.await_args_list] == [
        message.id,
        message.id,
    ]


@pytest.mark.asyncio
async def test_incremental_stream_latches_first_chunk_and_allows_completion():
    bus = ResponseBus()
    interaction = Interaction()
    seen = []

    async def on_message(message):
        seen.append(message)

    await bus.subscribe("s1", on_message, receive_chunks=True)
    await bus.publish(
        session_id="s1",
        content="Your order ships Tuesday.",
        channel="default",
        stream=True,
        streaming_complete=False,
        interaction=interaction,
        interaction_id=interaction.id,
        category="user",
    )
    assert interaction.has_emitted() is True

    await bus.publish(
        session_id="s1",
        content="",
        channel="default",
        stream=True,
        streaming_complete=True,
        interaction=interaction,
        interaction_id=interaction.id,
        category="user",
    )
    await bus.publish(
        session_id="s1",
        content="Duplicate fallback.",
        channel="default",
        interaction=interaction,
        interaction_id=interaction.id,
        category="user",
    )

    assert [message.message_type for message in seen] == ["stream_chunk", "final"]
    assert "".join(message.content for message in seen) == "Your order ships Tuesday."
    assert interaction.response == "Your order ships Tuesday."


@pytest.mark.asyncio
async def test_nonstream_during_open_stream_does_not_mint_a_second_identity():
    """A live user accumulator owns the turn.

    Non-stream publish() mints a fresh Object id. If that is allowed while
    chunks are in flight, a downstream translator splits on the new id and the
    browser shows two assistant bubbles for one answer.
    """
    bus = ResponseBus()
    interaction = Interaction()
    seen = []

    async def on_message(message):
        seen.append(message)

    await bus.subscribe("s1", on_message, receive_chunks=True)
    await bus.publish(
        session_id="s1",
        content="Hello from the stream.",
        channel="default",
        stream=True,
        streaming_complete=False,
        interaction=interaction,
        interaction_id=interaction.id,
        category="user",
    )
    stream_id = seen[0].id

    await bus.publish(
        session_id="s1",
        content="Hello from the stream.",
        channel="default",
        interaction=interaction,
        interaction_id=interaction.id,
        category="user",
    )

    user_ids = {message.id for message in seen if message.category == "user"}
    assert user_ids == {stream_id}
    assert [message.message_type for message in seen] == ["stream_chunk"]


@pytest.mark.asyncio
async def test_nonstream_while_gate_holds_does_not_mint_a_second_identity():
    """First stream call may create the accumulator without latching (empty /
    withheld chunk). Non-stream publish must still not invent a second id.
    """
    bus = ResponseBus()
    interaction = Interaction()
    seen = []

    async def on_message(message):
        seen.append(message)

    await bus.subscribe("s1", on_message, receive_chunks=True)
    await bus.publish(
        session_id="s1",
        content="",
        channel="default",
        stream=True,
        streaming_complete=False,
        interaction=interaction,
        interaction_id=interaction.id,
        category="user",
    )
    assert interaction.has_emitted() is False
    assert interaction.id in bus._adhoc_accumulation

    await bus.publish(
        session_id="s1",
        content="The withheld answer.",
        channel="default",
        interaction=interaction,
        interaction_id=interaction.id,
        category="user",
    )

    assert seen == []
    assert interaction.has_emitted() is False


@pytest.mark.asyncio
async def test_finalize_does_not_mint_a_second_user_identity():
    """Streaming already enqueues message_type=final under acc.message_id.
    finalize_interaction must not emit another user-category frame with a
    new Object id — adapters may treat that as a message boundary.
    """
    bus = ResponseBus()
    interaction = Interaction()
    seen = []

    async def on_message(message):
        seen.append(message)

    await bus.subscribe("s1", on_message, receive_chunks=True)
    await bus.publish(
        session_id="s1",
        content="Ships Tuesday.",
        channel="default",
        stream=True,
        streaming_complete=False,
        interaction=interaction,
        interaction_id=interaction.id,
        category="user",
    )
    await bus.publish(
        session_id="s1",
        content="",
        channel="default",
        stream=True,
        streaming_complete=True,
        interaction=interaction,
        interaction_id=interaction.id,
        category="user",
    )
    ids_after_stream = {message.id for message in seen}
    assert len(ids_after_stream) == 1

    await bus.finalize_interaction(
        interaction_id=interaction.id,
        interaction=interaction,
        session_id="s1",
        channel="default",
    )

    assert {message.id for message in seen} == ids_after_stream
