"""Conversational vs processing gate decisions for CockpitInteractAction."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jvagent.action.cockpit.routing_types import RoutingResult


def should_use_conversational_gate(
    routing: "RoutingResult", *, converse_enabled: bool
) -> bool:
    """True when Phase 2 should use the Persona conversational path only.

    Strict semantics: only ``CONVERSATIONAL`` intent uses the persona-only path.
    Every other intent (including UNCLEAR / INTERACTIVE with no recommended
    skills) goes through the cockpit engine, because the engine has harness
    tools (memory, artifacts, task planning, conversation search) that work
    regardless of skill match.
    """
    if not converse_enabled:
        return False
    return routing.intent_type == "CONVERSATIONAL"


def should_enter_processing_gate(
    routing: "RoutingResult", *, converse_enabled: bool
) -> bool:
    """True when Phase 2 should run the processing / cockpit path."""
    return not should_use_conversational_gate(
        routing, converse_enabled=converse_enabled
    )
