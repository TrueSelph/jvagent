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
