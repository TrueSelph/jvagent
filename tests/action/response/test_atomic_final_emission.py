"""Process-wide egress claim: one delivered user final per interaction."""

from __future__ import annotations

import pytest

from jvagent.action.response.response_bus import (
    ResponseBus,
    clear_interaction_egress,
)
from jvagent.memory.interaction import Interaction


@pytest.fixture(autouse=True)
def _clear_egress():
    clear_interaction_egress()
    yield
    clear_interaction_egress()


def _clone_interaction(src: Interaction) -> Interaction:
    """Fresh Python object with the same id and an unset emitted latch."""
    clone = Interaction()
    object.__setattr__(clone, "id", src.id)
    clone.emitted = False
    clone.response = ""
    return clone


@pytest.mark.asyncio
async def test_rematerialized_interaction_cannot_emit_a_second_hello():
    bus = ResponseBus()
    interaction = Interaction()
    seen = []

    async def on_message(message):
        seen.append(message)

    await bus.subscribe("s1", on_message, receive_chunks=True)
    await bus.publish(
        session_id="s1",
        content="Hello! I'm Integral's assistant.",
        channel="default",
        interaction=interaction,
        interaction_id=interaction.id,
        category="user",
    )
    twin = _clone_interaction(interaction)
    assert twin.has_emitted() is False
    await bus.publish(
        session_id="s1",
        content="Hello! I'm Integral's assistant.",
        channel="default",
        interaction=twin,
        interaction_id=interaction.id,
        category="user",
    )

    user_text = [
        m.content
        for m in seen
        if m.category == "user" and m.message_type != "final" and m.content
    ]
    assert user_text == ["Hello! I'm Integral's assistant."]


@pytest.mark.asyncio
async def test_two_buses_fresh_session_hello_one_final():
    """Fresh session Hello: two ResponseBus instances, one interaction.

    Exactly one persisted response and one delivered final across both
    subscriber lists.
    """
    bus_a = ResponseBus()
    bus_b = ResponseBus()
    interaction = Interaction()
    seen_a = []
    seen_b = []

    async def on_a(message):
        seen_a.append(message)

    async def on_b(message):
        seen_b.append(message)

    await bus_a.subscribe("s1", on_a, receive_chunks=True)
    await bus_b.subscribe("s1", on_b, receive_chunks=True)

    await bus_a.publish(
        session_id="s1",
        content="Hello! How can I help?",
        channel="default",
        stream=True,
        streaming_complete=False,
        interaction=interaction,
        interaction_id=interaction.id,
        category="user",
    )
    await bus_a.publish(
        session_id="s1",
        content="",
        channel="default",
        stream=True,
        streaming_complete=True,
        interaction=interaction,
        interaction_id=interaction.id,
        category="user",
    )
    twin = _clone_interaction(interaction)
    await bus_b.publish(
        session_id="s1",
        content="Hello! How can I help?",
        channel="default",
        interaction=twin,
        interaction_id=interaction.id,
        category="user",
    )
    await bus_a.finalize_interaction(
        interaction_id=interaction.id,
        interaction=interaction,
        session_id="s1",
        channel="default",
    )
    await bus_b.finalize_interaction(
        interaction_id=interaction.id,
        interaction=twin,
        session_id="s1",
        channel="default",
    )

    combined = seen_a + seen_b
    user_text = [
        m.content
        for m in combined
        if m.category == "user" and m.message_type == "stream_chunk" and m.content
    ]
    finals = [m for m in combined if m.category == "user" and m.message_type == "final"]
    adhoc = [
        m.content
        for m in combined
        if m.category == "user" and m.message_type == "adhoc" and m.content
    ]
    assert user_text == ["Hello! How can I help?"]
    assert adhoc == []
    assert len(finals) == 1
    assert interaction.response == "Hello! How can I help?"
    assert finals[0].id == seen_a[0].id
