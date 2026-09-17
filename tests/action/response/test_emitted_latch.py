"""Bus delivery latches interaction.emitted for user content only."""

from __future__ import annotations

import pytest

from jvagent.action.response.response_bus import ResponseBus
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
