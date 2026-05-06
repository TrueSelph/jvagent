"""Conversational vs processing gate decisions for CockpitInteractAction."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jvagent.action.cockpit.routing_types import RoutingResult


def should_use_conversational_gate(
    routing: "RoutingResult", *, converse_enabled: bool
) -> bool:
    """True when Phase 2 should use the Persona conversational path only."""
    if not converse_enabled:
        return False
    if routing.intent_type in ("INFORMATIONAL", "DIRECTIVE"):
        return False
    return routing.intent_type == "CONVERSATIONAL" or not routing.actions


def should_enter_processing_gate(
    routing: "RoutingResult", *, converse_enabled: bool
) -> bool:
    """True when Phase 2 should run the processing / cockpit path."""
    return not should_use_conversational_gate(
        routing, converse_enabled=converse_enabled
    )
