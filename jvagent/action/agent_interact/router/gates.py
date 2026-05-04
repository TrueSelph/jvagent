"""Explicit conversational vs processing gate decisions for ``AgentInteractAction``."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jvagent.action.router.routing_result import RoutingResult


def should_use_conversational_gate(
    routing: "RoutingResult", *, converse_enabled: bool
) -> bool:
    """True when Phase 2 should use the Persona conversational path only."""
    if not converse_enabled:
        return False
    # INFORMATIONAL and DIRECTIVE require tool grounding; these intents MUST
    # use the skill loop so the model can activate skills via skill_search or
    # always_active. The persona path has no tool evidence and would hallucinate.
    if routing.intent_type in ("INFORMATIONAL", "DIRECTIVE"):
        return False
    return routing.intent_type == "CONVERSATIONAL" or not routing.actions


def should_enter_processing_gate(
    routing: "RoutingResult", *, converse_enabled: bool
) -> bool:
    """True when Phase 2 should run the agentic / processing path."""
    return not should_use_conversational_gate(
        routing, converse_enabled=converse_enabled
    )
