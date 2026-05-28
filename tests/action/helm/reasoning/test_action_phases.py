"""Tests for ``ReasoningHelm`` phase dispatch + walker-revisit mechanics.

Focus: state-machine glue between Phase 1 (routing) and Phase 2 (engine step
loop), the stale-interaction guard, and the ``_handle_step_result`` branching
that drives the walker-revisit pattern. The router and engine are mocked so
the tests exercise only the action's dispatch logic.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.bridge.state import BRIDGE_STATE_VISITOR_ATTR, BridgeState
from jvagent.action.helm.reasoning.context import EngineStepResult
from jvagent.action.helm.reasoning.contracts import TerminationReason
from jvagent.action.helm.reasoning.reasoning_helm import ReasoningHelm
from jvagent.action.helm.reasoning.session import SESSION_KEY, get_session

pytestmark = pytest.mark.asyncio


def _make_action(monkeypatch) -> ReasoningHelm:
    """Build a ReasoningHelm instance bypassing graph-level wiring.

    The model class restricts attribute setting to declared fields, so we
    monkeypatch class methods rather than mutating instances.
    """
    action = ReasoningHelm()
    monkeypatch.setattr(ReasoningHelm, "_ensure_interaction", lambda self, v: True)

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(ReasoningHelm, "publish", _noop)
    monkeypatch.setattr(ReasoningHelm, "publish_thought", _noop)
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
    # Wave-2 H3: ReasoningHelm's per-turn orchestration state
    # (``step_outcome``, ``pending_final_emit``) now lives in
    # ``bridge_state.helm_states[helm_name]`` instead of on the
    # singleton. Tests that drive ``_orchestrate`` / ``_handle_step_result``
    # directly must stamp a real BridgeState on the visitor so the
    # helpers can read/write a per-turn dict.
    setattr(visitor, BRIDGE_STATE_VISITOR_ATTR, BridgeState(turn_started_at=0.0))
    return visitor


# ---------------------------------------------------------------------------
# Stale interaction guard
# ---------------------------------------------------------------------------


async def test_stale_interaction_clears_state_and_reroutes(monkeypatch):
    """Engine in state from a different interaction_id → session reset, route rerun."""
    action = _make_action(monkeypatch)
    visitor = _make_visitor("int_NEW")
    sess = get_session(visitor)
    sess.engine = MagicMock()
    sess.interaction_id = "int_OLD"
    sess.debug_state = MagicMock()

    route_called = {"v": False}

    async def _fake_phase_route(self, v):
        route_called["v"] = True
        # Verify session was reset by the guard before route fires.
        s = get_session(v)
        assert s.engine is None
        assert s.debug_state is None
        assert s.interaction_id is None
        # Signal terminal so step() returns without further work.
        # Per Wave-2 H3 the outcome lives in bridge_state.helm_states.
        self._set_step_outcome(v, "yield")

    monkeypatch.setattr(
        ReasoningHelm,
        "_phase_route_and_setup",
        _fake_phase_route,
    )

    # ReasoningHelm is driven by Bridge via step(visitor, bridge_state). Call
    # the orchestration body directly to exercise the stale-state guard
    # without needing a full Bridge harness here.
    await action._orchestrate(visitor)
    assert route_called["v"] is True


async def test_revisit_with_engine_skips_routing_and_runs_continue(monkeypatch):
    """Engine present + interaction_id matches → _phase_continue, not _phase_route_and_setup."""
    action = _make_action(monkeypatch)
    visitor = _make_visitor("int_1")
    sess = get_session(visitor)
    sess.engine = MagicMock()
    sess.interaction_id = "int_1"

    calls = []

    async def _fake_route(self, v):
        calls.append("route")

    async def _fake_continue(self, v):
        calls.append("continue")

    monkeypatch.setattr(
        ReasoningHelm,
        "_phase_route_and_setup",
        _fake_route,
    )
    monkeypatch.setattr(
        ReasoningHelm,
        "_phase_continue",
        _fake_continue,
    )

    await action._orchestrate(visitor)
    assert calls == ["continue"]
    # Helms do not unrecord — they are invisible to the walker's action trace.


# ---------------------------------------------------------------------------
# _handle_step_result branching
# ---------------------------------------------------------------------------


async def test_handle_step_result_tool_calls_sets_continue_outcome(monkeypatch):
    """status='tool_calls' → state persisted + _step_outcome='continue'.

    Helms do NOT mutate the walker queue directly; ``_step_outcome`` is the
    signal :meth:`ReasoningHelm.step` reads to translate into the CONTINUE
    verb that Bridge interprets.
    """
    action = _make_action(monkeypatch)
    visitor = _make_visitor()
    engine = MagicMock()
    engine.save_state.return_value = MagicMock()

    result = EngineStepResult(status="tool_calls", iterations=1, duration_seconds=0.1)
    await action._handle_step_result(visitor, engine, result)

    assert get_session(visitor).debug_state is engine.save_state.return_value
    # Wave-2 H3 — outcome read from bridge_state.helm_states slot.
    assert action._get_step_outcome(visitor) == "continue"
    visitor.prepend.assert_not_called()  # Bridge owns queue mutations
    visitor.interaction.set_to_executed.assert_not_called()


async def test_handle_step_result_finalize_flag_terminates_without_revisit(monkeypatch):
    """tool_calls + session.finalized=True → no revisit, interaction set to executed."""
    action = _make_action(monkeypatch)
    visitor = _make_visitor()
    sess = get_session(visitor)
    sess.finalized = True
    engine = MagicMock()
    engine.save_state.return_value = MagicMock()

    result = EngineStepResult(status="tool_calls", iterations=1, duration_seconds=0.1)
    await action._handle_step_result(visitor, engine, result)

    visitor.prepend.assert_not_called()
    visitor.interaction.set_to_executed.assert_called_once()
    # session reset → finalized + debug_state cleared
    s_after = get_session(visitor)
    assert s_after.finalized is False
    assert s_after.debug_state is None


async def test_handle_step_result_terminal_stashes_pending_emit(monkeypatch):
    """Terminal status (final_response) → session reset, ``_pending_final_emit``
    populated for :meth:`step` to surface as EMIT(via_persona=True).

    Phase-2 distillation pushed ``deliver_final_response`` up into Bridge:
    ReasoningHelm no longer invokes the delivery helper directly. Instead
    it stashes the engine's final text and activated_skills, then
    :meth:`step` converts that into an EMIT verb. Bridge's
    ``_handle_emit`` checks ``via_persona=True`` and runs persona
    stylisation via ``deliver_via_persona``.
    """
    action = _make_action(monkeypatch)
    visitor = _make_visitor()
    sess = get_session(visitor)
    sess.engine = MagicMock()
    sess.interaction_id = "int_1"
    sess.debug_state = MagicMock()
    engine = MagicMock()

    result = EngineStepResult(
        status="final_response",
        final_response="hello",
        termination_reason=TerminationReason.COMPLETED,
        iterations=1,
        duration_seconds=0.5,
        activated_skills=["web_search"],
    )

    # Required attributes the action reads at terminal time.
    action.response_mode = "publish"
    action.degenerate_response_max_chars = 25

    await action._handle_step_result(visitor, engine, result)

    # Session reset — all cockpit-owned fields are back to defaults.
    s_after = get_session(visitor)
    assert s_after.engine is None
    assert s_after.interaction_id is None
    assert s_after.debug_state is None
    visitor.interaction.set_to_executed.assert_called_once()
    # New contract: pending emit stashed for step() to surface.
    # Wave-2 H3 — buffer lives in bridge_state.helm_states.
    pending = action._get_pending_final_emit(visitor)
    assert pending is not None
    assert pending["text"] == "hello"
    assert pending["activated_skills"] == ["web_search"]


async def test_handle_step_result_terminal_empty_response_no_pending_emit(monkeypatch):
    """Terminal with empty/whitespace final_response → no pending emit stashed."""
    action = _make_action(monkeypatch)
    visitor = _make_visitor()
    sess = get_session(visitor)
    sess.engine = MagicMock()
    engine = MagicMock()

    result = EngineStepResult(
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
    # No final response → no pending emit (step() will return YIELD).
    # Wave-2 H3 — read from bridge_state.helm_states slot.
    assert action._get_pending_final_emit(visitor) is None
