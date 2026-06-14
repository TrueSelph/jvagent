"""Orchestrator history event inclusion via include_history_events."""

from __future__ import annotations

import pytest

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)

pytestmark = pytest.mark.asyncio


@pytest.mark.asyncio
async def test_history_includes_events_by_default(make_visitor):
    ex = OrchestratorInteractAction()
    ex.history_limit = 20

    visitor = make_visitor()
    await ex._history(visitor)

    visitor.conversation.get_interaction_history.assert_awaited_once()
    assert (
        visitor.conversation.get_interaction_history.call_args.kwargs["with_event"]
        is True
    )


@pytest.mark.asyncio
async def test_history_omits_events_when_flag_disabled(make_visitor):
    ex = OrchestratorInteractAction()
    ex.history_limit = 20
    ex.include_history_events = False

    visitor = make_visitor()
    await ex._history(visitor)

    assert (
        visitor.conversation.get_interaction_history.call_args.kwargs["with_event"]
        is False
    )
