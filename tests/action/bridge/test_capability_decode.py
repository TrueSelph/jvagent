"""Tests for ADR-0008 capability decode + dispatch regime classification.

Covers:

- ``CapabilityRef`` shape (frozen, immutable, kind constrained).
- ``decode_dispatch_plan`` correctly classifies the four regimes.
- ``RoutingResult.selected`` round-trips through ``to_dict`` / ``from_dict``.
- Backcompat properties (``actions`` / ``skills`` / ``interact_actions``)
  derive from ``selected``.
"""

from __future__ import annotations

import pytest

from jvagent.action.helm.reasoning.routing.types import (
    CapabilityRef,
    DispatchRegime,
    RoutingResult,
    decode_dispatch_plan,
)


class TestCapabilityRefShape:
    def test_capability_ref_is_frozen(self):
        cap = CapabilityRef(name="web_search", kind="skill")
        with pytest.raises((AttributeError, Exception)):
            cap.name = "other"  # type: ignore[misc]

    def test_capability_ref_equality(self):
        a = CapabilityRef(name="web_search", kind="skill")
        b = CapabilityRef(name="web_search", kind="skill")
        assert a == b

    def test_capability_ref_kind_distinguishes_same_name(self):
        a = CapabilityRef(name="x", kind="skill")
        b = CapabilityRef(name="x", kind="ia")
        assert a != b


class TestDispatchRegimeClassification:
    """``decode_dispatch_plan`` covers all four regimes."""

    def test_skills_only(self):
        routing = RoutingResult(
            selected=[
                CapabilityRef(name="web_search", kind="skill"),
                CapabilityRef(name="pageindex_search", kind="skill"),
            ]
        )
        plan = decode_dispatch_plan(routing)
        assert plan.regime == DispatchRegime.SKILLS_ONLY
        assert len(plan.skills) == 2
        assert plan.ias == []

    def test_ias_only(self):
        routing = RoutingResult(
            selected=[CapabilityRef(name="HandoffInteractAction", kind="ia")]
        )
        plan = decode_dispatch_plan(routing)
        assert plan.regime == DispatchRegime.IAS_ONLY
        assert plan.skills == []
        assert len(plan.ias) == 1
        assert plan.ias[0].name == "HandoffInteractAction"

    def test_mixed(self):
        routing = RoutingResult(
            selected=[
                CapabilityRef(name="web_search", kind="skill"),
                CapabilityRef(name="HandoffInteractAction", kind="ia"),
            ]
        )
        plan = decode_dispatch_plan(routing)
        assert plan.regime == DispatchRegime.MIXED
        assert len(plan.skills) == 1
        assert len(plan.ias) == 1

    def test_none_empty_selection(self):
        routing = RoutingResult(selected=[])
        plan = decode_dispatch_plan(routing)
        assert plan.regime == DispatchRegime.NONE
        assert plan.skills == []
        assert plan.ias == []


class TestRoutingResultSerialisation:
    """``selected`` survives the to_dict / from_dict round-trip."""

    def test_round_trip_skills_only(self):
        original = RoutingResult(
            selected=[CapabilityRef(name="web_search", kind="skill")],
            interpretation="user wants to search the web",
            intent_type="INFORMATIONAL",
            confidence=0.9,
        )
        restored = RoutingResult.from_dict(original.to_dict())
        assert restored.selected == original.selected
        assert restored.intent_type == "INFORMATIONAL"

    def test_round_trip_mixed(self):
        original = RoutingResult(
            selected=[
                CapabilityRef(name="web_search", kind="skill"),
                CapabilityRef(name="HandoffInteractAction", kind="ia"),
            ],
            intent_type="DIRECTIVE",
        )
        restored = RoutingResult.from_dict(original.to_dict())
        assert restored.selected == original.selected

    def test_legacy_split_schema_parses_into_selected(self):
        """Pre-Wave-6 payloads with split skills / interact_actions still parse."""
        payload = {
            "intent_type": "INFORMATIONAL",
            "skills": ["web_search"],
            "interact_actions": ["HandoffInteractAction"],
            "confidence": 0.8,
        }
        result = RoutingResult.from_dict(payload)
        assert {(c.name, c.kind) for c in result.selected} == {
            ("web_search", "skill"),
            ("HandoffInteractAction", "ia"),
        }

    def test_legacy_actions_only_payload(self):
        """Even older payloads with only an ``actions`` list still parse."""
        payload = {
            "intent_type": "INFORMATIONAL",
            "actions": ["web_search"],
        }
        result = RoutingResult.from_dict(payload)
        assert result.selected == [CapabilityRef(name="web_search", kind="skill")]


class TestRoutingResultBackcompatProperties:
    """``actions`` / ``skills`` / ``interact_actions`` derive from ``selected``."""

    def test_actions_property_filters_skills(self):
        routing = RoutingResult(
            selected=[
                CapabilityRef(name="s1", kind="skill"),
                CapabilityRef(name="IA1", kind="ia"),
                CapabilityRef(name="s2", kind="skill"),
            ]
        )
        assert routing.actions == ["s1", "s2"]

    def test_skills_is_alias_of_actions(self):
        routing = RoutingResult(
            selected=[CapabilityRef(name="s1", kind="skill")]
        )
        assert routing.skills == routing.actions == ["s1"]

    def test_interact_actions_property_filters_ias(self):
        routing = RoutingResult(
            selected=[
                CapabilityRef(name="s1", kind="skill"),
                CapabilityRef(name="IA1", kind="ia"),
                CapabilityRef(name="IA2", kind="ia"),
            ]
        )
        assert routing.interact_actions == ["IA1", "IA2"]

    def test_empty_selection_yields_empty_lists(self):
        routing = RoutingResult()
        assert routing.actions == []
        assert routing.skills == []
        assert routing.interact_actions == []


class TestConversationalInvariant:
    """CONVERSATIONAL intent must force an empty ``selected``."""

    def test_conversational_clears_selected_from_parser(self):
        from jvagent.action.helm.reasoning.routing.types import (
            parse_routing_response,
        )

        response = (
            '{"intent_type": "CONVERSATIONAL", '
            '"selected": [{"name": "web_search", "kind": "skill"}], '
            '"confidence": 0.9}'
        )
        result = parse_routing_response(response)
        assert result.selected == []
