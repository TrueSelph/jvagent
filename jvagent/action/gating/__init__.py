"""Response gating for realistic conversational behavior.

ResponseGatingAction classifies utterances as RESPOND, SUPPRESS, or DEFER
to determine when the agent should reply, stay silent, or accumulate fragments.
"""

from jvagent.action.gating.gating_result import (
    GatingResult,
    POSTURE_DEFER,
    POSTURE_RESPOND,
    POSTURE_SUPPRESS,
    parse_gating_response,
)
from jvagent.action.gating.response_gating_action import ResponseGatingAction

__all__ = [
    "GatingResult",
    "POSTURE_DEFER",
    "POSTURE_RESPOND",
    "POSTURE_SUPPRESS",
    "parse_gating_response",
    "ResponseGatingAction",
]
