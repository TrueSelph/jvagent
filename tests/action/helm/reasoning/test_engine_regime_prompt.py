"""Tests for regime-aware engine persona-addendum assembly (ADR-0008).

:meth:`ReasoningHelm._start_engine` composes the engine's persona prompt by
appending a regime-specific addendum to ``persona_description``.
:func:`build_engine_persona_addendum` is the helper; this test pins its
contract across the four regimes.
"""

from __future__ import annotations

import pytest

from jvagent.action.helm.reasoning.reasoning_helm import (
    IA_CHAIN_AWARENESS,
    build_engine_persona_addendum,
)
from jvagent.action.helm.reasoning.routing.types import (
    CapabilityRef,
    DispatchPlan,
    DispatchRegime,
    RoutingResult,
    decode_dispatch_plan,
)


def _routing(intent: str = "INFORMATIONAL", interpretation: str = "") -> RoutingResult:
    return RoutingResult(intent_type=intent, interpretation=interpretation)


@pytest.mark.parametrize(
    "regime,selected,expect_skill_guidance,expect_ia_awareness",
    [
        (
            DispatchRegime.SKILLS_ONLY,
            [CapabilityRef(name="web_search", kind="skill")],
            True,
            False,
        ),
        (
            DispatchRegime.MIXED,
            [
                CapabilityRef(name="web_search", kind="skill"),
                CapabilityRef(name="HandoffInteractAction", kind="ia"),
            ],
            True,
            True,
        ),
        (
            DispatchRegime.IAS_ONLY,
            [CapabilityRef(name="HandoffInteractAction", kind="ia")],
            # IAS_ONLY skips the engine; the helper is documented to
            # return empty for it (the helm never calls it on this regime,
            # but the helper must remain safe to call defensively).
            False,
            False,
        ),
        (
            DispatchRegime.NONE,
            [],
            False,
            False,
        ),
    ],
)
def test_engine_persona_addendum_per_regime(
    regime,
    selected,
    expect_skill_guidance,
    expect_ia_awareness,
):
    routing = RoutingResult(selected=selected, intent_type="INFORMATIONAL")
    plan = decode_dispatch_plan(routing)
    # Confirm the regime under test matches what decode_dispatch_plan produces.
    assert plan.regime == regime
    addendum = build_engine_persona_addendum(plan, routing)

    if expect_skill_guidance:
        assert "Routing decision" in addendum
        assert "Router pre-selected skill(s)" in addendum
    else:
        assert "Router pre-selected skill(s)" not in addendum

    if expect_ia_awareness:
        assert IA_CHAIN_AWARENESS in addendum
    else:
        assert IA_CHAIN_AWARENESS not in addendum


def test_skills_only_addendum_lists_routed_skills_by_name():
    routing = RoutingResult(
        selected=[
            CapabilityRef(name="web_search", kind="skill"),
            CapabilityRef(name="pageindex_search", kind="skill"),
        ],
        intent_type="INFORMATIONAL",
    )
    plan = decode_dispatch_plan(routing)
    addendum = build_engine_persona_addendum(plan, routing)
    assert "web_search" in addendum
    assert "pageindex_search" in addendum


def test_mixed_addendum_contains_both_blocks_in_order():
    routing = RoutingResult(
        selected=[
            CapabilityRef(name="web_search", kind="skill"),
            CapabilityRef(name="HandoffInteractAction", kind="ia"),
        ],
        intent_type="DIRECTIVE",
    )
    plan = decode_dispatch_plan(routing)
    addendum = build_engine_persona_addendum(plan, routing)
    skill_idx = addendum.find("Router pre-selected skill(s)")
    ia_idx = addendum.find(IA_CHAIN_AWARENESS.strip())
    assert skill_idx != -1 and ia_idx != -1
    # Skill guidance comes before IA awareness so the engine reads "do your
    # work" before "stay brief for the follow-up flow".
    assert skill_idx < ia_idx


def test_none_regime_returns_empty_addendum():
    routing = RoutingResult(selected=[], intent_type="UNCLEAR")
    plan = decode_dispatch_plan(routing)
    addendum = build_engine_persona_addendum(plan, routing)
    assert addendum == ""


def test_skills_only_with_empty_skills_returns_empty():
    """Defensive: an empty SKILLS_ONLY plan (shouldn't happen but possible
    if regime is forged in a test) yields no addendum because the helper
    short-circuits on empty skill list."""
    plan = DispatchPlan(regime=DispatchRegime.SKILLS_ONLY, skills=[], ias=[])
    routing = RoutingResult()
    addendum = build_engine_persona_addendum(plan, routing)
    assert addendum == ""


def test_interpretation_text_is_interpolated():
    routing = RoutingResult(
        selected=[CapabilityRef(name="web_search", kind="skill")],
        intent_type="INFORMATIONAL",
        interpretation="user wants up-to-date pricing data",
    )
    plan = decode_dispatch_plan(routing)
    addendum = build_engine_persona_addendum(plan, routing)
    assert "user wants up-to-date pricing data" in addendum
