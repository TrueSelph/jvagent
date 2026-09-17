"""model_error egress must not double-deliver when the latch is already set."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.action.response.response_bus import ResponseBus
from jvagent.memory.interaction import Interaction


def test_turn_delivered_honors_emitted_before_response():
    interaction = Interaction()
    interaction.mark_emitted()
    assert OrchestratorInteractAction._turn_delivered(interaction) is True
    assert not (interaction.response or "").strip()


def test_turn_delivered_honors_response_before_emitted_latch():
    interaction = Interaction()
    interaction.set_response("already here")
    assert OrchestratorInteractAction._turn_delivered(interaction) is True
    assert interaction.has_emitted() is False


@pytest.mark.asyncio
async def test_after_loop_skips_model_unavailable_when_emitted_latched(monkeypatch):
    """Stream can latch ``emitted`` before ``response`` is flushed — _after_loop
    must not queue a second model_unavailable compose."""
    ex = OrchestratorInteractAction()
    interaction = Interaction(utterance="hi")
    interaction.mark_emitted()
    visitor = MagicMock()
    visitor.interaction = interaction
    send_reply = AsyncMock()
    monkeypatch.setattr(ex, "_send_reply", send_reply)

    state = MagicMock()
    state.ended_via = "model_error"
    state.observations = []
    state.last_obs_len = 0
    state.last_dec_meta = None
    await ex._after_loop(visitor, state)
    send_reply.assert_not_called()


@pytest.mark.asyncio
async def test_commit_pending_adhoc_skips_when_response_already_set():
    bus = ResponseBus()
    interaction = Interaction(session_id="s1", user_id="u1", utterance="hi")
    interaction.set_response("Hello — model unavailable.")
    acc = bus._get_or_create_accumulator(
        interaction_id=interaction.id,
        session_id="s1",
        channel="default",
        user_id="u1",
        metadata={},
        category="user",
    )
    acc.chunks = ["Hello — model unavailable."]

    await bus.commit_pending_adhoc(interaction.id, interaction)

    assert interaction.id not in bus._adhoc_accumulation
    assert interaction.response == "Hello — model unavailable."
