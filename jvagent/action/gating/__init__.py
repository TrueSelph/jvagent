"""Response gating for realistic conversational behavior.

ResponseGatingInteractAction classifies utterances as RESPOND, SUPPRESS, or DEFER
to determine when the agent should reply, stay silent, or accumulate fragments.
"""

from jvagent.action.gating.gating_result import (
    POSTURE_DEFER,
    POSTURE_RESPOND,
    POSTURE_SUPPRESS,
    GatingResult,
    parse_gating_response,
)
from jvagent.action.gating.response_gating import ResponseGatingInteractAction

__all__ = [
    "GatingResult",
    "POSTURE_DEFER",
    "POSTURE_RESPOND",
    "POSTURE_SUPPRESS",
    "parse_gating_response",
    "ResponseGatingInteractAction",
]
