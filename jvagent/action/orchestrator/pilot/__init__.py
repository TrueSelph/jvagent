"""Opt-in Pydantic AI capability pilot for skill-driven execution.

Importing :mod:`jvagent` or the legacy Orchestrator does not import this
package. Install ``jvagent[pydantic-pilot]`` before selecting the pilot driver.
"""

from .contracts import (
    EvidenceReference,
    PilotCaller,
    PilotRunContext,
    PilotSnapshot,
    ResearchBrief,
)
from .state import PILOT_TASK_TYPE, PilotStateError, PilotTaskStore

__all__ = [
    "EvidenceReference",
    "PilotCaller",
    "PilotRunContext",
    "PilotSnapshot",
    "ResearchBrief",
    "PILOT_TASK_TYPE",
    "PilotStateError",
    "PilotTaskStore",
]
