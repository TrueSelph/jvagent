"""Orchestrator loop history: events omitted; reply cap does not clip history."""

from __future__ import annotations

import pytest

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)


@pytest.mark.asyncio
async def test_history_omits_events_by_default_and_honors_with_event_knob(
    make_visitor,
):
    """Loop history excludes [EVENT] lines unless the ``with_event`` knob is
    enabled (ADR-0053; opt-in via agent.yaml ``context:`` or channel_overrides)."""
    ex = OrchestratorInteractAction()
    ex.history_limit = 20
    assert ex.with_event is False

    visitor = make_visitor()
    await ex._history(visitor)

    visitor.conversation.get_interaction_history.assert_awaited_once()
    assert (
        visitor.conversation.get_interaction_history.call_args.kwargs["with_event"]
        is False
    )

    ex.with_event = True
    visitor = make_visitor()
    await ex._history(visitor)
    assert (
        visitor.conversation.get_interaction_history.call_args.kwargs["with_event"]
        is True
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


@pytest.mark.asyncio
async def test_with_event_honours_channel_override(make_visitor):
    """A channel may differ from the action-level value in both directions."""
    ex = OrchestratorInteractAction()
    ex.with_event = False
    ex.channel_overrides = {"whatsapp": {"with_event": True}}

    visitor = make_visitor()
    visitor.channel = "whatsapp"
    await ex._history(visitor)
    kwargs = visitor.conversation.get_interaction_history.call_args.kwargs
    assert kwargs["with_event"] is True

    visitor = make_visitor()
    visitor.channel = "web"
    await ex._history(visitor)
    kwargs = visitor.conversation.get_interaction_history.call_args.kwargs
    assert kwargs["with_event"] is False

    ex.with_event = True
    ex.channel_overrides = {"whatsapp": {"with_event": False}}
    visitor = make_visitor()
    visitor.channel = "whatsapp"
    await ex._history(visitor)
    kwargs = visitor.conversation.get_interaction_history.call_args.kwargs
    assert kwargs["with_event"] is False


@pytest.mark.asyncio
async def test_event_lines_carry_the_same_per_statement_cap(make_visitor):
    """An [EVENT] line is free-form text with no length contract of its own, and
    history is resent on every tick — so it is bounded by the same knob that
    bounds a replayed reply (ADR-0053), and released by the same ``0``."""
    ex = OrchestratorInteractAction()
    ex.with_event = True

    visitor = make_visitor()
    await ex._history(visitor)
    kwargs = visitor.conversation.get_interaction_history.call_args.kwargs
    assert kwargs["max_event_length"] == ex.history_statement_max_chars

    ex.history_statement_max_chars = 0
    visitor = make_visitor()
    await ex._history(visitor)
    kwargs = visitor.conversation.get_interaction_history.call_args.kwargs
    assert kwargs["max_event_length"] is None
