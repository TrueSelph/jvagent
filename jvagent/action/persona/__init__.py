"""PersonaAction module for agent behavioral modeling.

This module provides the PersonaAction, a core action for agent behavioral modeling
with LLM-driven parameters, action delegation, and an event bus for asynchronous
response handling.

Key Components:
- PersonaAction: Core action class with interact() method
- PersonaActionResult: Result container with event bus access
- InteractionEventBus: Event bus for async response handling
- PersonaParameter: Behavioral parameter definition
- ResponseAggregator: Collects events into final result

Inspired by the Parlant PersonaInteractAction pattern.
"""

from jvagent.action.persona.base import PersonaAction, PersonaActionResult
from jvagent.action.persona.events import (
    InteractionEvent,
    InteractionEventBus,
    InteractionEventType,
    ResponseAggregator,
)
from jvagent.action.persona.parameter import (
    DEFAULT_BASE_PARAMETERS,
    ParameterManager,
    PersonaParameter,
)

# Import endpoints for automatic discovery
from jvagent.action.persona import endpoints  # noqa: F401

__all__ = [
    "PersonaAction",
    "PersonaActionResult",
    "InteractionEvent",
    "InteractionEventBus",
    "InteractionEventType",
    "ResponseAggregator",
    "PersonaParameter",
    "ParameterManager",
    "DEFAULT_BASE_PARAMETERS",
]
