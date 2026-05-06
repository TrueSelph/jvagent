"""Tests for ``CockpitInteractAction`` phase dispatch + walker-revisit mechanics.

Focus: state-machine glue between Phase 1 (routing) and Phase 2 (engine step
loop), the stale-interaction guard, and the ``_handle_step_result`` branching
that drives the walker-revisit pattern. The router and engine are mocked so
the tests exercise only the action's dispatch logic.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.cockpit.cockpit_interact_action import (
    _COCKPIT_ENGINE_KEY,
    _COCKPIT_INTERACTION_ID_KEY,
    _COCKPIT_STATE_KEY,
    CockpitInteractAction,
)
from jvagent.action.cockpit.context import CockpitStepResult
from jvagent.action.cockpit.contracts import TerminationReason


pytestmark = pytest.mark.asyncio


def _make_action(monkeypatch) -> CockpitInteractAction:
    """Build a CockpitInteractAction instance bypassing graph-level wiring.

    The model class restricts attribute setting to declared fields, so we
    monkeypatch class methods rather than mutating instances.
    """
    action = CockpitInteractAction()
    monkeypatch.setattr(
        CockpitInteractAction, "_ensure_interaction", lambda self, v: True
    )

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(CockpitInteractAction, "publish", _noop)
    monkeypatch.setattr(CockpitInteractAction, "publish_thought", _noop)
    return action


def _make_visitor(interaction_id: str = "int_1") -> Any:
    interaction = MagicMock()
    interaction.id = interaction_id
    interaction.set_to_executed = MagicMock()
    interaction.response = ""
    interaction.save = AsyncMock()

    conversation = MagicMock()

    visitor = MagicMock()
    visitor.interaction = interaction
    visitor.conversation = conversation
    visitor._skill_state = {}
    visitor.prepend = AsyncMock()
    visitor.unrecord_action_execution = AsyncMock()
    return visitor


# ---------------------------------------------------------------------------
# Stale interaction guard
# ---------------------------------------------------------------------------


async def test_stale_interaction_clears_state_and_reroutes(monkeypatch):
    """Engine in state from a different interaction_id → state cleared, route rerun."""
    action = _make_action(monkeypatch)
    visitor = _make_visitor("int_NEW")
    stale_engine = MagicMock()
    visitor._skill_state[_COCKPIT_ENGINE_KEY] = stale_engine
    visitor._skill_state[_COCKPIT_INTERACTION_ID_KEY] = "int_OLD"
    visitor._skill_state[_COCKPIT_STATE_KEY] = MagicMock()

    route_called = {"v": False}

    async def _fake_phase_route(self, v):
        route_called["v"] = True
        # Verify state was cleared by the guard before route fires.
        assert _COCKPIT_ENGINE_KEY not in v._skill_state
        assert _COCKPIT_STATE_KEY not in v._skill_state
        assert _COCKPIT_INTERACTION_ID_KEY not in v._skill_state

    monkeypatch.setattr(
        CockpitInteractAction,
        "_phase_route_and_setup",
        _fake_phase_route,
    )

    await action.execute(visitor)
    assert route_called["v"] is True


async def test_revisit_with_engine_skips_routing_and_runs_continue(monkeypatch):
    """Engine present + interaction_id matches → _phase_continue, not _phase_route_and_setup."""
    action = _make_action(monkeypatch)
    visitor = _make_visitor("int_1")
    visitor._skill_state[_COCKPIT_ENGINE_KEY] = MagicMock()
    visitor._skill_state[_COCKPIT_INTERACTION_ID_KEY] = "int_1"

    calls = []

    async def _fake_route(self, v):
        calls.append("route")

    async def _fake_continue(self, v):
        calls.append("continue")

    monkeypatch.setattr(
        CockpitInteractAction,
        "_phase_route_and_setup",
        _fake_route,
    )
    monkeypatch.setattr(
        CockpitInteractAction,
        "_phase_continue",
        _fake_continue,
    )

    await action.execute(visitor)
    assert calls == ["continue"]
    visitor.unrecord_action_execution.assert_awaited()


# ---------------------------------------------------------------------------
# _handle_step_result branching
# ---------------------------------------------------------------------------


async def test_handle_step_result_tool_calls_prepends_self_for_revisit(monkeypatch):
    """status='tool_calls' → state persisted + visitor.prepend([self])."""
    action = _make_action(monkeypatch)
    visitor = _make_visitor()
    engine = MagicMock()
    engine.save_state.return_value = MagicMock()

    result = CockpitStepResult(status="tool_calls", iterations=1, duration_seconds=0.1)
    await action._handle_step_result(visitor, engine, result)

    assert visitor._skill_state.get(_COCKPIT_STATE_KEY) is engine.save_state.return_value
    visitor.prepend.assert_awaited_with([action])
    visitor.interaction.set_to_executed.assert_not_called()


async def test_handle_step_result_finalize_flag_terminates_without_revisit(monkeypatch):
    """tool_calls + cockpit_finalized=True → no revisit, interaction set to executed."""
    action = _make_action(monkeypatch)
    visitor = _make_visitor()
    visitor._skill_state["cockpit_finalized"] = True
    engine = MagicMock()
    engine.save_state.return_value = MagicMock()

    result = CockpitStepResult(status="tool_calls", iterations=1, duration_seconds=0.1)
    await action._handle_step_result(visitor, engine, result)

    visitor.prepend.assert_not_called()
    visitor.interaction.set_to_executed.assert_called_once()
    # cockpit_finalized + cockpit_state cleared
    assert "cockpit_finalized" not in visitor._skill_state
    assert _COCKPIT_STATE_KEY not in visitor._skill_state


async def test_handle_step_result_terminal_clears_engine_state(monkeypatch):
    """Terminal status (final_response) → engine + state keys cleared, delivery invoked."""
    action = _make_action(monkeypatch)
    visitor = _make_visitor()
    visitor._skill_state[_COCKPIT_ENGINE_KEY] = MagicMock()
    visitor._skill_state[_COCKPIT_INTERACTION_ID_KEY] = "int_1"
    visitor._skill_state[_COCKPIT_STATE_KEY] = MagicMock()
    engine = MagicMock()

    delivery_called = {"v": False}

    async def _fake_deliver(action, visitor, result, **kwargs):
        delivery_called["v"] = True

    monkeypatch.setattr(
        "jvagent.action.cockpit.cockpit_interact_action.deliver_final_response",
        _fake_deliver,
    )

    # Non-empty final_response so deliver_final_response is invoked.
    result = CockpitStepResult(
        status="final_response",
        final_response="hello",
        termination_reason=TerminationReason.COMPLETED,
        iterations=1,
        duration_seconds=0.5,
    )

    # Required attributes the action reads at terminal time.
    action.response_mode = "publish"
    action.degenerate_response_max_chars = 25

    await action._handle_step_result(visitor, engine, result)

    # All cockpit state keys cleared.
    assert _COCKPIT_ENGINE_KEY not in visitor._skill_state
    assert _COCKPIT_INTERACTION_ID_KEY not in visitor._skill_state
    assert _COCKPIT_STATE_KEY not in visitor._skill_state
    visitor.interaction.set_to_executed.assert_called_once()
    assert delivery_called["v"] is True


async def test_handle_step_result_terminal_empty_response_skips_delivery(monkeypatch):
    """Terminal with empty/whitespace final_response → state cleared, delivery skipped."""
    action = _make_action(monkeypatch)
    visitor = _make_visitor()
    visitor._skill_state[_COCKPIT_ENGINE_KEY] = MagicMock()
    engine = MagicMock()

    delivery_called = {"v": False}

    async def _fake_deliver(*args, **kwargs):
        delivery_called["v"] = True

    monkeypatch.setattr(
        "jvagent.action.cockpit.cockpit_interact_action.deliver_final_response",
        _fake_deliver,
    )

    result = CockpitStepResult(
        status="final_response",
        final_response="   ",
        termination_reason=TerminationReason.COMPLETED,
        iterations=1,
        duration_seconds=0.0,
    )
    action.response_mode = "publish"
    action.degenerate_response_max_chars = 25

    await action._handle_step_result(visitor, engine, result)

    visitor.interaction.set_to_executed.assert_called_once()
    assert delivery_called["v"] is False
