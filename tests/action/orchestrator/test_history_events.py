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
    """Reply soft-cap must not silently truncate prior turns in the loop prompt."""
    ex = OrchestratorInteractAction()
    ex.history_limit = 8
    ex.max_statement_length = 120

    visitor = make_visitor()
    await ex._history(visitor)

    kwargs = visitor.conversation.get_interaction_history.call_args.kwargs
    assert kwargs["max_statement_length"] is None
    assert kwargs["with_event"] is False
