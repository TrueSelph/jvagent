"""Executive + Centers pattern (ADR-0010).

A brain-shaped agent composition: one :class:`ExecutiveInteractAction`
(prefrontal cortex) recruits specialist :class:`BaseCenter` leaves (skills, IA)
and voices through a Persona center, on a frame-stack control loop. Ships as a
peer to the Rails pattern — no harness changes.
"""

from jvagent.action.executive.base import BaseCenter
from jvagent.action.executive.context import TurnContext
from jvagent.action.executive.contracts import (
    ACTIVATE,
    RESPOND,
    RETURN,
    STEP,
    YIELD,
    Brief,
    Result,
)
from jvagent.action.executive.executive_interact_action import (
    ExecutiveInteractAction,
)
from jvagent.action.executive.state import WorkingMemory

__all__ = [
    "ExecutiveInteractAction",
    "BaseCenter",
    "TurnContext",
    "WorkingMemory",
    "Brief",
    "Result",
    "ACTIVATE",
    "RESPOND",
    "YIELD",
    "STEP",
    "RETURN",
]
