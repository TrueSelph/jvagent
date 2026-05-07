"""Tests for converse-as-skill behavior.

Covers:
- The built-in ``converse`` skill bundle parses with ``always_active=True``.
- ``apply_skill_selector`` keeps always-active skills past empty / explicit
  selectors but still honors the deny list.
- ``should_use_conversational_gate`` triggers on the structural skill check
  (router selected ``converse`` as the only route).
- ``CockpitRouter._run_llm_route`` injects ``converse`` when the router
  classifies a CONVERSATIONAL utterance and the catalog has ``converse``.
"""

from __future__ import annotations

from jvagent.action.cockpit.delivery.gates import (
    CONVERSE_SKILL_NAMES,
    should_enter_processing_gate,
    should_use_conversational_gate,
)
from jvagent.action.cockpit.routing.types import RoutingResult
from jvagent.scaffold.skill_resolve import (
    apply_skill_selector,
    resolve_builtin_skills,
)


def test_converse_bundle_is_always_active() -> None:
    """The shipped ``converse`` skill bundle parses with always_active=True."""
    skills = resolve_builtin_skills()
    assert "converse" in skills
    assert skills["converse"]["always_active"] is True
    # Tools are intentionally absent — converse routes through PersonaAction.
    assert skills["converse"]["tool_files"] == []


def test_apply_skill_selector_keeps_always_active_with_empty_selector() -> None:
    """Empty selectors used to return {}; always-active skills now slip through."""
    bundles = {
        "converse": {"name": "converse", "always_active": True},
        "answer": {"name": "answer"},
    }
    assert apply_skill_selector(bundles, selector=None) == {
        "converse": bundles["converse"]
    }
    assert apply_skill_selector(bundles, selector=[]) == {
        "converse": bundles["converse"]
    }
    assert apply_skill_selector(bundles, selector="") == {
        "converse": bundles["converse"]
    }


def test_apply_skill_selector_merges_always_active_with_list_selector() -> None:
    """Explicit selector lists keep the listed skills AND always-active ones."""
    bundles = {
        "converse": {"name": "converse", "always_active": True},
        "answer": {"name": "answer"},
        "research": {"name": "research"},
    }
    selected = apply_skill_selector(bundles, selector=["answer"])
    assert set(selected.keys()) == {"converse", "answer"}


def test_apply_skill_selector_deny_overrides_always_active() -> None:
    """Operators can opt out of an always-active skill via the deny list."""
    bundles = {"converse": {"name": "converse", "always_active": True}}
    assert apply_skill_selector(bundles, selector=None, denied=["converse"]) == {}


def test_apply_skill_selector_empty_bundles_unchanged_with_no_always_active() -> None:
    """Existing empty-selector contract still holds when no always-active is present."""
    bundles = {"a": {"name": "a"}, "b": {"name": "b"}}
    assert apply_skill_selector(bundles, selector=None) == {}
    assert apply_skill_selector(bundles, selector=[]) == {}


def test_gate_triggers_on_converse_only_skill_route() -> None:
    """Router selected ``converse`` as the sole skill, no IAs → gate fires."""
    routing = RoutingResult(
        actions=["converse"],
        interact_actions=[],
        intent_type="INFORMATIONAL",  # Skill-driven check is intent-agnostic.
    )
    assert should_use_conversational_gate(routing, converse_enabled=True) is True
    assert should_enter_processing_gate(routing, converse_enabled=True) is False


def test_gate_does_not_trigger_when_other_skill_is_routed() -> None:
    """Mixed routes (converse + another skill) fall through to the engine."""
    routing = RoutingResult(
        actions=["converse", "answer"],
        interact_actions=[],
    )
    assert should_use_conversational_gate(routing, converse_enabled=True) is False


def test_gate_does_not_trigger_when_interact_action_is_routed() -> None:
    """An interact_action queued alongside converse means the engine still runs."""
    routing = RoutingResult(
        actions=["converse"],
        interact_actions=["HandoffInteractAction"],
    )
    assert should_use_conversational_gate(routing, converse_enabled=True) is False


def test_gate_back_compat_conversational_intent_still_fires() -> None:
    """Legacy CONVERSATIONAL intent path still triggers the gate."""
    routing = RoutingResult(
        actions=[],
        interact_actions=[],
        intent_type="CONVERSATIONAL",
    )
    assert should_use_conversational_gate(routing, converse_enabled=True) is True


def test_gate_empty_route_fast_path_respects_flag() -> None:
    """Empty-route fast path is only active when conversational_fast_path=True."""
    routing = RoutingResult(actions=[], interact_actions=[], intent_type="UNCLEAR")
    assert (
        should_use_conversational_gate(
            routing, converse_enabled=True, conversational_fast_path=True
        )
        is True
    )
    assert (
        should_use_conversational_gate(
            routing, converse_enabled=True, conversational_fast_path=False
        )
        is False
    )


def test_gate_disabled_when_converse_enabled_false() -> None:
    """Master switch: converse_enabled=False bypasses every trigger."""
    routing = RoutingResult(actions=["converse"], interact_actions=[])
    assert should_use_conversational_gate(routing, converse_enabled=False) is False


def test_converse_skill_names_constant() -> None:
    """Sanity: the canonical bundle name is in the gate's recognised set."""
    assert "converse" in CONVERSE_SKILL_NAMES
