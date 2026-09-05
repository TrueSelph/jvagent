"""Orchestrator loop history: events omitted; reply cap does not clip history."""

from __future__ import annotations

import pytest

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)


@pytest.mark.asyncio
async def test_history_omits_events(make_visitor):
    ex = OrchestratorInteractAction()
    ex.history_limit = 20

    visitor = make_visitor()
    await ex._history(visitor)

    visitor.conversation.get_interaction_history.assert_awaited_once()
    assert (
        visitor.conversation.get_interaction_history.call_args.kwargs["with_event"]
        is False
    )


@pytest.mark.asyncio
async def test_history_ignores_reply_max_statement_length(make_visitor):
    """The reply soft-cap must not clip prior turns; the loop has its own,
    much larger, per-statement bound (``history_statement_max_chars``)."""
    ex = OrchestratorInteractAction()
    ex.history_limit = 8
    ex.max_statement_length = 120

    visitor = make_visitor()
    await ex._history(visitor)

    kwargs = visitor.conversation.get_interaction_history.call_args.kwargs
    assert kwargs["max_statement_length"] == ex.history_statement_max_chars
    assert kwargs["max_statement_length"] != 120
    assert kwargs["with_event"] is False


@pytest.mark.asyncio
async def test_history_statement_cap_is_bounded_by_default_and_can_be_disabled(
    make_visitor,
):
    """Every tick resends the history, so an unbounded prior reply is billed on
    every step — the default caps each statement; ``0`` disables the cap."""
    ex = OrchestratorInteractAction()
    assert ex.history_statement_max_chars > 0

    visitor = make_visitor()
    await ex._history(visitor)
    kwargs = visitor.conversation.get_interaction_history.call_args.kwargs
    assert kwargs["max_statement_length"] == ex.history_statement_max_chars

    ex.history_statement_max_chars = 0
    visitor = make_visitor()
    await ex._history(visitor)
    kwargs = visitor.conversation.get_interaction_history.call_args.kwargs
    assert kwargs["max_statement_length"] is None
