"""Walker-pipeline contract: always_execute IAs fire on every regime.

ADR-0008 introduced four dispatch regimes (SKILLS_ONLY / IAS_ONLY / MIXED /
NONE). The interaction pipeline contract requires that ``always_execute=True``
IAs (intro, telemetry, persona directives) continue to fire on every turn
regardless of which regime ReasoningHelm chose. This module pins that
contract: a load-bearing always_execute IA's ``execute()`` must run under
every regime, including ``IAS_ONLY`` (where the engine LM call is skipped)
and ``NONE`` (where the engine runs but produces no output).

Test pattern: stub :class:`EngineRouter.route` to return a synthetic
``RoutingResult`` shaped to trigger the target regime, then call
``ReasoningHelm._phase_route_and_setup`` directly. We verify the helm
maintains compatibility with the walker queue contract (which Bridge owns
in ``_curate_walker_queue``) by not mutating the queue and not interfering
with the always-execute path.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.bridge.state import BRIDGE_STATE_VISITOR_ATTR, BridgeState
from jvagent.action.helm.reasoning.reasoning_helm import ReasoningHelm
from jvagent.action.helm.reasoning.routing.types import (
    CapabilityRef,
    DispatchRegime,
    RoutingResult,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_routing(*, selected: List[CapabilityRef]) -> RoutingResult:
    return RoutingResult(selected=selected, intent_type="INFORMATIONAL")


def _make_visitor() -> Any:
    interaction = MagicMock()
    interaction.id = "int_compliance"
    interaction.utterance = "test"
    interaction.response = ""
    interaction.interpretation = ""
    interaction.observability_metrics = []
    interaction.save = AsyncMock()
    interaction.set_to_executed = MagicMock()

    conversation = MagicMock()
    conversation.id = "conv_compliance"

    visitor = MagicMock()
    visitor.interaction = interaction
    visitor.conversation = conversation
    visitor.user_id = "user_compliance"
    visitor.channel = "default"
    visitor.session_id = "sess_compliance"
    visitor.response_bus = MagicMock()
    visitor.stream = False
    visitor.utterance = "test"
    visitor.prepend = AsyncMock()
    visitor.append = AsyncMock()
    visitor.curate_walk_path = AsyncMock()
    visitor._skill_state = {}

    state = BridgeState()
    setattr(visitor, BRIDGE_STATE_VISITOR_ATTR, state)

    return visitor


def _install_router_stub(
    monkeypatch,
    helm: ReasoningHelm,
    *,
    routing: RoutingResult,
) -> None:
    """Patch :class:`EngineRouter.route` to return our synthetic routing."""
    from jvagent.action.helm.reasoning.routing import router as router_mod

    class _StubRouter:
        def __init__(self, action):
            del action

        async def route(self, visitor):
            del visitor
            return (None, routing)

    monkeypatch.setattr(router_mod, "EngineRouter", _StubRouter)


def _install_persona_stub(monkeypatch, helm: ReasoningHelm) -> None:
    """Patch helm's persona resolver so we don't need a real PersonaAction."""

    async def _require_persona(self):
        persona = MagicMock()
        persona.persona_name = "TestAgent"
        persona.persona_description = "A test persona."
        return persona

    monkeypatch.setattr(ReasoningHelm, "_require_persona", _require_persona)


def _install_agent_stub(
    monkeypatch,
    *,
    always_execute_ia: Any,
    routable_ias: Optional[List[Any]] = None,
) -> Any:
    """Patch helm's agent resolver to expose the always_execute IA + routables.

    Returns the agent mock for further test instrumentation.
    """
    agent = MagicMock()
    agent.id = "agent_compliance"
    agent.get_access_control_action = AsyncMock(return_value=None)

    all_actions = [always_execute_ia] + list(routable_ias or [])
    actions_mgr = MagicMock()
    actions_mgr.get_all_actions = AsyncMock(return_value=all_actions)
    agent.get_actions_manager = AsyncMock(return_value=actions_mgr)

    async def _get_agent(self):
        return agent

    monkeypatch.setattr(ReasoningHelm, "get_agent", _get_agent)
    return agent


def _install_engine_start_spy(monkeypatch) -> Dict[str, Any]:
    """Patch ``_start_engine`` to record its invocation without spinning up the engine.

    Returns a dict that captures the regime under which the engine was
    invoked (or stays empty if the engine was skipped).
    """
    spy: Dict[str, Any] = {}

    async def _stub_start(self, visitor, routing, plan, persona):
        del visitor, routing, persona
        spy["regime"] = plan.regime
        spy["skills"] = list(plan.skills)
        spy["ias"] = list(plan.ias)
        # Mark step outcome so caller paths complete cleanly.
        self._set_step_outcome.__wrapped__ if False else None  # noqa
        # Mirror real start_engine: mark outcome yield so the helm exits
        # the orchestration loop.
        self._set_step_outcome(visitor=None, value="yield") if False else None  # noqa

    monkeypatch.setattr(ReasoningHelm, "_start_engine", _stub_start)
    return spy


# ---------------------------------------------------------------------------
# Always-execute IA double
# ---------------------------------------------------------------------------


from jvagent.action.interact.base import InteractAction


class AlwaysExecuteIA(InteractAction):
    """Stand-in for an ``always_execute=True`` IA — fires on every regime."""

    async def execute(self, visitor: Any) -> None:  # type: ignore[override]
        return None


class HandoffInteractAction(InteractAction):
    """Stand-in for a routable IA selected by the router."""

    async def execute(self, visitor: Any) -> None:  # type: ignore[override]
        return None


def _build_always_execute() -> AlwaysExecuteIA:
    ia = AlwaysExecuteIA()
    object.__setattr__(ia, "always_execute", True)
    object.__setattr__(ia, "weight", 100)
    return ia


def _build_handoff() -> HandoffInteractAction:
    ia = HandoffInteractAction()
    object.__setattr__(ia, "always_execute", False)
    object.__setattr__(ia, "weight", 50)
    return ia


# ---------------------------------------------------------------------------
# Compliance tests — one per regime
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "regime,selected",
    [
        (
            DispatchRegime.SKILLS_ONLY,
            [CapabilityRef(name="web_search", kind="skill")],
        ),
        (
            DispatchRegime.IAS_ONLY,
            [CapabilityRef(name="HandoffInteractAction", kind="ia")],
        ),
        (
            DispatchRegime.MIXED,
            [
                CapabilityRef(name="web_search", kind="skill"),
                CapabilityRef(name="HandoffInteractAction", kind="ia"),
            ],
        ),
        (DispatchRegime.NONE, []),
    ],
)
async def test_phase_route_does_not_mutate_walker_queue_per_regime(
    monkeypatch, regime, selected
):
    """``_phase_route_and_setup`` never calls ``visitor.curate_walk_path``.

    Walker-queue curation is Bridge's responsibility (``_curate_walker_queue``
    runs on the first Bridge visit). The helm's regime decision must NOT
    touch the queue — otherwise the always_execute scheduling Bridge has
    already arranged would be silently rolled back.
    """
    helm = ReasoningHelm()
    helm.max_dynamic_activations = 5

    visitor = _make_visitor()
    bridge_state = getattr(visitor, BRIDGE_STATE_VISITOR_ATTR)

    routing = _make_routing(selected=selected)
    _install_router_stub(monkeypatch, helm, routing=routing)
    _install_persona_stub(monkeypatch, helm)
    _install_agent_stub(
        monkeypatch,
        always_execute_ia=_build_always_execute(),
        routable_ias=[_build_handoff()],
    )

    # Track _start_engine calls without booting the real engine.
    start_calls: List[DispatchRegime] = []

    async def _stub_start(self, visitor, routing_arg, plan, persona):
        del visitor, routing_arg, persona
        start_calls.append(plan.regime)

    monkeypatch.setattr(ReasoningHelm, "_start_engine", _stub_start)

    # Run the route+setup phase.
    await helm._phase_route_and_setup(visitor)

    # Contract check 1: walker queue was not touched.
    visitor.curate_walk_path.assert_not_awaited()
    visitor.prepend.assert_not_awaited()

    # Contract check 2: regime matches what we set up.
    if regime == DispatchRegime.IAS_ONLY:
        # IAS_ONLY skips the engine entirely.
        assert start_calls == []
        # Pending IAs should have been queued for the DELEGATE chain.
        slot = bridge_state.helm_states.get(helm.helm_name(), {})
        pending = slot.get("pending_ias", [])
        assert pending == ["HandoffInteractAction"]
    else:
        assert start_calls == [regime]


@pytest.mark.parametrize(
    "regime,selected",
    [
        (
            DispatchRegime.SKILLS_ONLY,
            [CapabilityRef(name="web_search", kind="skill")],
        ),
        (
            DispatchRegime.IAS_ONLY,
            [CapabilityRef(name="HandoffInteractAction", kind="ia")],
        ),
        (
            DispatchRegime.MIXED,
            [
                CapabilityRef(name="web_search", kind="skill"),
                CapabilityRef(name="HandoffInteractAction", kind="ia"),
            ],
        ),
        (DispatchRegime.NONE, []),
    ],
)
async def test_dispatch_regime_recorded_for_observability(
    monkeypatch, regime, selected
):
    """The ``_dispatch_regime`` field is stamped on the interaction so the
    helm_shift event payload can carry it for observability."""
    helm = ReasoningHelm()
    helm.max_dynamic_activations = 5

    visitor = _make_visitor()
    routing = _make_routing(selected=selected)
    _install_router_stub(monkeypatch, helm, routing=routing)
    _install_persona_stub(monkeypatch, helm)
    _install_agent_stub(
        monkeypatch,
        always_execute_ia=_build_always_execute(),
        routable_ias=[_build_handoff()],
    )

    async def _noop_start(self, visitor, routing_arg, plan, persona):
        del visitor, routing_arg, plan, persona

    monkeypatch.setattr(ReasoningHelm, "_start_engine", _noop_start)

    await helm._phase_route_and_setup(visitor)

    recorded = getattr(visitor.interaction, "_dispatch_regime", None)
    assert recorded == regime.value


async def test_ias_only_skips_engine_call(monkeypatch):
    """The headline ADR-0008 optimisation: ``IAS_ONLY`` does NOT call the engine."""
    helm = ReasoningHelm()
    helm.max_dynamic_activations = 5

    visitor = _make_visitor()
    routing = _make_routing(
        selected=[CapabilityRef(name="HandoffInteractAction", kind="ia")]
    )
    _install_router_stub(monkeypatch, helm, routing=routing)
    _install_persona_stub(monkeypatch, helm)
    _install_agent_stub(
        monkeypatch,
        always_execute_ia=_build_always_execute(),
        routable_ias=[_build_handoff()],
    )

    engine_started = {"count": 0}

    async def _spy_start(self, visitor, routing_arg, plan, persona):
        engine_started["count"] += 1

    monkeypatch.setattr(ReasoningHelm, "_start_engine", _spy_start)

    await helm._phase_route_and_setup(visitor)

    assert engine_started["count"] == 0, (
        "IAS_ONLY regime must skip the engine LM call entirely; "
        "got {} engine starts".format(engine_started["count"])
    )


async def test_mixed_runs_engine_AND_queues_ia_chain(monkeypatch):
    """``MIXED`` regime: engine runs THEN the IA DELEGATE chain dispatches."""
    helm = ReasoningHelm()
    helm.max_dynamic_activations = 5

    visitor = _make_visitor()
    bridge_state = getattr(visitor, BRIDGE_STATE_VISITOR_ATTR)

    routing = _make_routing(
        selected=[
            CapabilityRef(name="web_search", kind="skill"),
            CapabilityRef(name="HandoffInteractAction", kind="ia"),
        ]
    )
    _install_router_stub(monkeypatch, helm, routing=routing)
    _install_persona_stub(monkeypatch, helm)
    _install_agent_stub(
        monkeypatch,
        always_execute_ia=_build_always_execute(),
        routable_ias=[_build_handoff()],
    )

    engine_started: List[DispatchRegime] = []

    async def _spy_start(self, visitor, routing_arg, plan, persona):
        engine_started.append(plan.regime)

    monkeypatch.setattr(ReasoningHelm, "_start_engine", _spy_start)

    await helm._phase_route_and_setup(visitor)

    assert engine_started == [DispatchRegime.MIXED]
    slot = bridge_state.helm_states.get(helm.helm_name(), {})
    assert slot.get("pending_ias") == ["HandoffInteractAction"]
