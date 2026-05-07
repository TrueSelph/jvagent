"""Unit tests for cockpit delegation helpers and dispatch branches.

Covers:
- ``resolve_routed_interact_actions``: class name → enabled InteractAction instances.
- ``collect_always_execute_interact_actions``: filters by ``always_execute=True``.
- ``curate_walk_path_for_cockpit``: combines + sorts + delegates to walker.
- ``prepend_routed_interact_actions``: front-of-queue insertion.
- ``CockpitInteractAction._phase_route_and_setup`` dispatch matrix:
  IA-only / skills-only / both.
- ``_handle_step_result`` "both" mode: prepends pending IAs on terminal.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.cockpit.cockpit_interact_action import (
    _COCKPIT_ENGINE_KEY,
    _COCKPIT_PENDING_IAS_KEY,
    CockpitInteractAction,
)
from jvagent.action.cockpit.context import CockpitStepResult
from jvagent.action.cockpit.contracts import TerminationReason
from jvagent.action.cockpit.delivery import delegation
from jvagent.action.cockpit.routing.types import POSTURE_RESPOND, RoutingResult
from jvagent.action.interact.base import InteractAction

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


def _make_ia_double(
    cls_name: str,
    *,
    weight: int = 0,
    always_execute: bool = False,
    enabled: bool = True,
) -> MagicMock:
    """Build a MagicMock that ``isinstance(_, InteractAction)`` returns True for."""
    fake = MagicMock(spec=InteractAction)
    fake.__class__ = type(cls_name, (InteractAction,), {})
    fake.weight = weight
    fake.always_execute = always_execute
    fake.enabled = enabled
    fake.id = f"ia_{cls_name.lower()}"
    return fake


def _make_agent_with_actions(actions):
    actions_mgr = MagicMock()
    actions_mgr.get_all_actions = AsyncMock(return_value=actions)
    agent = MagicMock()
    agent.id = "agent_test"
    agent.get_actions_manager = AsyncMock(return_value=actions_mgr)
    return agent


def _make_visitor():
    visitor = MagicMock()
    visitor._skill_state = {}
    visitor.curate_walk_path = AsyncMock()
    visitor.prepend = AsyncMock()
    visitor.append = AsyncMock()
    visitor.unrecord_action_execution = AsyncMock()
    interaction = MagicMock()
    interaction.id = "int_1"
    interaction.set_to_executed = MagicMock()
    visitor.interaction = interaction
    visitor.conversation = MagicMock()
    return visitor


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


async def test_resolve_routed_filters_disabled_and_unknown():
    """Resolver returns only enabled IAs whose class names appear in routing."""
    intro = _make_ia_double("IntroInteractAction", weight=-101, always_execute=True)
    handoff = _make_ia_double("HandoffInteractAction", weight=10)
    agent = _make_agent_with_actions([intro, handoff])

    routing = RoutingResult(
        posture=POSTURE_RESPOND,
        interact_actions=["HandoffInteractAction", "DoesNotExistAction"],
    )
    matched = await delegation.resolve_routed_interact_actions(agent, routing)
    assert [m.__class__.__name__ for m in matched] == ["HandoffInteractAction"]


async def test_resolve_routed_filters_self_class():
    """Resolver excludes the cockpit class even if router returned it."""
    cockpit = _make_ia_double("CockpitInteractAction", weight=-200)
    intro = _make_ia_double("IntroInteractAction", weight=-101)
    agent = _make_agent_with_actions([cockpit, intro])

    routing = RoutingResult(
        posture=POSTURE_RESPOND,
        interact_actions=["CockpitInteractAction", "IntroInteractAction"],
    )
    matched = await delegation.resolve_routed_interact_actions(agent, routing)
    assert [m.__class__.__name__ for m in matched] == ["IntroInteractAction"]


async def test_resolve_routed_sorts_by_weight():
    a = _make_ia_double("AlphaIA", weight=10)
    b = _make_ia_double("BetaIA", weight=-5)
    c = _make_ia_double("GammaIA", weight=0)
    agent = _make_agent_with_actions([a, b, c])

    routing = RoutingResult(
        posture=POSTURE_RESPOND,
        interact_actions=["AlphaIA", "GammaIA", "BetaIA"],
    )
    matched = await delegation.resolve_routed_interact_actions(agent, routing)
    assert [m.__class__.__name__ for m in matched] == ["BetaIA", "GammaIA", "AlphaIA"]


# ---------------------------------------------------------------------------
# Always-execute collector
# ---------------------------------------------------------------------------


async def test_collect_always_execute_filters_correctly():
    intro = _make_ia_double("IntroInteractAction", weight=-101, always_execute=True)
    handoff = _make_ia_double("HandoffInteractAction", weight=10, always_execute=False)
    cleanup = _make_ia_double("CleanupAction", weight=200, always_execute=True)
    agent = _make_agent_with_actions([intro, handoff, cleanup])

    matched = await delegation.collect_always_execute_interact_actions(agent)
    names = [m.__class__.__name__ for m in matched]
    assert "IntroInteractAction" in names
    assert "CleanupAction" in names
    assert "HandoffInteractAction" not in names
    # Weight ordering preserved
    assert names == sorted(
        names, key=lambda n: {"IntroInteractAction": -101, "CleanupAction": 200}[n]
    )


async def test_collect_always_execute_excludes_named_classes():
    cockpit = _make_ia_double("CockpitInteractAction", weight=-200, always_execute=True)
    intro = _make_ia_double("IntroInteractAction", weight=-101, always_execute=True)
    agent = _make_agent_with_actions([cockpit, intro])

    matched = await delegation.collect_always_execute_interact_actions(
        agent, exclude_class_names={"CockpitInteractAction"}
    )
    assert [m.__class__.__name__ for m in matched] == ["IntroInteractAction"]


# ---------------------------------------------------------------------------
# Walk path curation + prepend
# ---------------------------------------------------------------------------


async def test_curate_walk_path_combines_and_dedupes():
    visitor = _make_visitor()
    cockpit = _make_ia_double("CockpitInteractAction", weight=-200)
    intro = _make_ia_double("IntroInteractAction", weight=-101)
    handoff = _make_ia_double("HandoffInteractAction", weight=10)

    curated = await delegation.curate_walk_path_for_cockpit(
        visitor,
        cockpit_action=cockpit,
        routed=[handoff],
        always_execute=[intro],
    )

    names = [a.__class__.__name__ for a in curated]
    # Sorted by weight: cockpit(-200) → intro(-101) → handoff(10)
    assert names == [
        "CockpitInteractAction",
        "IntroInteractAction",
        "HandoffInteractAction",
    ]
    visitor.curate_walk_path.assert_awaited_once()


async def test_prepend_routed_handles_empty_list():
    visitor = _make_visitor()
    await delegation.prepend_routed_interact_actions(visitor, [])
    visitor.prepend.assert_not_called()


async def test_prepend_routed_inserts_in_weight_order():
    visitor = _make_visitor()
    handoff = _make_ia_double("HandoffInteractAction", weight=10)
    intro = _make_ia_double("IntroInteractAction", weight=-101)

    # Pass in unsorted; helper should sort by weight before prepending.
    await delegation.prepend_routed_interact_actions(visitor, [handoff, intro])

    visitor.prepend.assert_awaited_once()
    args, _ = visitor.prepend.call_args
    ordered = args[0]
    assert [a.__class__.__name__ for a in ordered] == [
        "IntroInteractAction",
        "HandoffInteractAction",
    ]


# ---------------------------------------------------------------------------
# _phase_route_and_setup dispatch matrix
# ---------------------------------------------------------------------------


def _patch_action(monkeypatch):
    """Build a minimal CockpitInteractAction with class methods stubbed."""
    monkeypatch.setattr(
        CockpitInteractAction, "_ensure_interaction", lambda self, v: True
    )
    monkeypatch.setattr(
        CockpitInteractAction, "_require_persona", AsyncMock(return_value=MagicMock())
    )
    return CockpitInteractAction()


async def _stub_router(monkeypatch, posture: str, routing: RoutingResult):
    """Patch CockpitRouter import inside _phase_route_and_setup to a stub."""

    class _StubRouter:
        def __init__(self, action):
            self._action = action

        async def route(self, visitor):
            return posture, routing

    monkeypatch.setattr(
        "jvagent.action.cockpit.routing.router.CockpitRouter",
        _StubRouter,
    )


async def test_phase_route_dispatches_ia_only_skips_engine(monkeypatch):
    """IA-only mode: engine skipped, walker queue curated to include IAs.

    The curate puts routed IAs in the walker queue in weight order — no
    explicit prepend is needed (and a prepend would cause duplicate visits).
    """
    action = _patch_action(monkeypatch)
    handoff = _make_ia_double("HandoffInteractAction", weight=10)
    agent = _make_agent_with_actions([handoff])

    monkeypatch.setattr(
        CockpitInteractAction, "get_agent", AsyncMock(return_value=agent)
    )

    routing = RoutingResult(
        posture=POSTURE_RESPOND,
        intent_type="DIRECTIVE",
        actions=[],  # no skills
        interact_actions=["HandoffInteractAction"],
    )
    await _stub_router(monkeypatch, "RESPOND", routing)

    # _start_cockpit should NOT be called in IA-only mode.
    start_called = {"v": False}

    async def _fake_start(self, visitor, routing, persona):
        start_called["v"] = True

    monkeypatch.setattr(CockpitInteractAction, "_start_cockpit", _fake_start)

    visitor = _make_visitor()
    await action._phase_route_and_setup(visitor)

    assert start_called["v"] is False
    visitor.curate_walk_path.assert_awaited()
    visitor.prepend.assert_not_called()
    # Cockpit appended itself to the END for the finalize-via-persona step;
    # the finalize step (not Phase 1) marks the interaction executed.
    visitor.append.assert_awaited()
    args, _ = visitor.append.call_args
    assert args[0] == [action]
    assert visitor._skill_state.get("cockpit_ia_finalize_pending") is True


async def test_phase_route_dispatches_skills_only_runs_engine(monkeypatch):
    action = _patch_action(monkeypatch)
    handoff = _make_ia_double("HandoffInteractAction", weight=10)
    agent = _make_agent_with_actions([handoff])

    monkeypatch.setattr(
        CockpitInteractAction, "get_agent", AsyncMock(return_value=agent)
    )

    routing = RoutingResult(
        posture=POSTURE_RESPOND,
        intent_type="DIRECTIVE",
        actions=["web_search"],  # skills only
        interact_actions=[],
    )
    await _stub_router(monkeypatch, "RESPOND", routing)

    start_called = {"v": False}

    async def _fake_start(self, visitor, routing, persona):
        start_called["v"] = True

    monkeypatch.setattr(CockpitInteractAction, "_start_cockpit", _fake_start)

    visitor = _make_visitor()
    await action._phase_route_and_setup(visitor)

    assert start_called["v"] is True
    # No pending IAs queued in skills-only mode
    assert _COCKPIT_PENDING_IAS_KEY not in visitor._skill_state


async def test_phase_route_dispatches_both_queues_pending_ias(monkeypatch):
    action = _patch_action(monkeypatch)
    handoff = _make_ia_double("HandoffInteractAction", weight=10)
    agent = _make_agent_with_actions([handoff])

    monkeypatch.setattr(
        CockpitInteractAction, "get_agent", AsyncMock(return_value=agent)
    )

    routing = RoutingResult(
        posture=POSTURE_RESPOND,
        intent_type="DIRECTIVE",
        actions=["web_search"],
        interact_actions=["HandoffInteractAction"],
    )
    await _stub_router(monkeypatch, "RESPOND", routing)

    start_called = {"v": False}

    async def _fake_start(self, visitor, routing, persona):
        start_called["v"] = True

    monkeypatch.setattr(CockpitInteractAction, "_start_cockpit", _fake_start)

    visitor = _make_visitor()
    await action._phase_route_and_setup(visitor)

    assert start_called["v"] is True
    pending = visitor._skill_state.get(_COCKPIT_PENDING_IAS_KEY) or []
    assert [a.__class__.__name__ for a in pending] == ["HandoffInteractAction"]


# ---------------------------------------------------------------------------
# _handle_step_result "both" mode handoff
# ---------------------------------------------------------------------------


async def test_finalize_pending_runs_persona_respond(monkeypatch):
    """Finalize-pending revisit (IA-only mode): cockpit dispatches via the
    unified persona delivery (which routes through ``action.respond()``)."""
    monkeypatch.setattr(
        CockpitInteractAction, "_ensure_interaction", lambda self, v: True
    )
    persona = MagicMock()
    persona.enabled = True
    persona.persona_description = "test persona"
    persona.respond = AsyncMock(return_value="final response")
    monkeypatch.setattr(
        CockpitInteractAction, "_require_persona", AsyncMock(return_value=persona)
    )

    # The unified delivery now goes through ``action.respond()`` (which
    # itself resolves PersonaAction at runtime). Stub it to assert the
    # finalize path engages the persona handoff exactly once.
    respond_mock = AsyncMock(return_value="final response")
    monkeypatch.setattr(CockpitInteractAction, "respond", respond_mock)

    action = CockpitInteractAction()
    visitor = _make_visitor()
    visitor._skill_state["cockpit_ia_finalize_pending"] = True

    await action.execute(visitor)

    respond_mock.assert_awaited_once()
    # Flag cleared
    assert "cockpit_ia_finalize_pending" not in visitor._skill_state
    visitor.interaction.set_to_executed.assert_called_once()
    # Finalize-pending revisit is a delivery shim — must unrecord to avoid
    # showing CockpitInteractAction twice in the actions trace.
    visitor.unrecord_action_execution.assert_awaited()


async def test_handle_step_result_terminal_clears_pending_ias(monkeypatch):
    """Terminal step in 'both' mode → state cleared.

    Pending IAs are NOT re-prepended at terminal — they're already in the walker
    queue from Phase 1 curate. Re-prepending would cause duplicate execution.
    """
    monkeypatch.setattr(
        CockpitInteractAction, "_ensure_interaction", lambda self, v: True
    )
    action = CockpitInteractAction()

    handoff = _make_ia_double("HandoffInteractAction", weight=10)
    visitor = _make_visitor()
    visitor._skill_state[_COCKPIT_ENGINE_KEY] = MagicMock()
    visitor._skill_state[_COCKPIT_PENDING_IAS_KEY] = [handoff]

    monkeypatch.setattr(
        "jvagent.action.cockpit.cockpit_interact_action.deliver_final_response",
        AsyncMock(),
    )

    result = CockpitStepResult(
        status="final_response",
        final_response="engine output",
        termination_reason=TerminationReason.COMPLETED,
        iterations=1,
        duration_seconds=0.1,
    )

    await action._handle_step_result(visitor, MagicMock(), result)

    # State cleared
    assert _COCKPIT_ENGINE_KEY not in visitor._skill_state
    assert _COCKPIT_PENDING_IAS_KEY not in visitor._skill_state
    # No prepend at terminal — IAs are already in walker queue from curate.
    visitor.prepend.assert_not_called()
