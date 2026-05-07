"""Conversational vs processing gate decisions for CockpitInteractAction.

The conversational gate is the cockpit's low-latency persona path. It
short-circuits the engine entirely and replies via a single PersonaAction
call. Two ways the gate triggers:

1. **Skill-driven** (preferred). The router selected the ``converse`` skill
   (or its alias) as the *only* skill, with no interact_actions queued. The
   converse skill's defining characteristic is "no tools, no engine — talk
   to PersonaAction" so the dispatch is structural.
2. **Intent fallback**. The router classified the utterance as
   ``CONVERSATIONAL`` (legacy path — kept for back-compat with routers that
   don't surface ``converse`` in their skill descriptors).
3. **Empty-route fast-path**. The router recommended no skills, no
   interact_actions, and ``conversational_fast_path`` is enabled — the
   engine has nothing specific to do, so we go straight to the persona.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from jvagent.action.cockpit.routing.types import RoutingResult


# Names that count as "the converse skill". ``converse`` is the canonical
# bundle name; aliases left in for forwards compatibility with operators
# who rename it via app-local skill bundles.
CONVERSE_SKILL_NAMES = ("converse",)


def _is_converse_only(skills: Iterable[str]) -> bool:
    names = [s for s in skills if s]
    if len(names) != 1:
        return False
    return names[0] in CONVERSE_SKILL_NAMES


def should_use_conversational_gate(
    routing: "RoutingResult",
    *,
    converse_enabled: bool,
    conversational_fast_path: bool = True,
) -> bool:
    """True when Phase 2 should take the persona-only path (skip the engine).

    See module docstring for the three trigger conditions.
    """
    if not converse_enabled:
        return False

    routed_actions = list(routing.actions or [])
    routed_ias = list(getattr(routing, "interact_actions", None) or [])

    # Trigger 1: structural — converse is the only routed skill, no IAs.
    if _is_converse_only(routed_actions) and not routed_ias:
        return True

    # Trigger 2: legacy intent classification.
    if routing.intent_type == "CONVERSATIONAL" and not routed_ias:
        return True

    # Trigger 3: empty-route fast-path.
    if conversational_fast_path and not routed_actions and not routed_ias:
        return True

    return False


def should_enter_processing_gate(
    routing: "RoutingResult",
    *,
    converse_enabled: bool,
    conversational_fast_path: bool = True,
) -> bool:
    """True when Phase 2 should run the processing / cockpit path."""
    return not should_use_conversational_gate(
        routing,
        converse_enabled=converse_enabled,
        conversational_fast_path=conversational_fast_path,
    )


__all__ = [
    "CONVERSE_SKILL_NAMES",
    "should_use_conversational_gate",
    "should_enter_processing_gate",
]
