"""Shared orchestrator constants (loop + main action module)."""

from __future__ import annotations

# Keys the model commonly uses to carry user-facing text, in priority order.
TEXT_KEYS = ("answer", "text", "content", "message", "reply", "response")

# Egress + indirection tools are never "steered".
STEER_EXEMPT = frozenset(
    {"reply", "respond", "find_tool", "load_tool", "find_skill", "use_skill"}
)
NON_SUBSTANTIVE_TOOLS = STEER_EXEMPT

# Decision keys that are control/text fields, never tool arguments.
DECISION_RESERVED_KEYS = frozenset(
    {
        "action",
        "tool",
        "args",
        "answer",
        "text",
        "content",
        "message",
        "reasoning",
        "thought",
        "name",
        "skill",
        "topic",
        "query",
    }
)

# Backward-compatible aliases for tests and internal imports.
_TEXT_KEYS = TEXT_KEYS
_STEER_EXEMPT = STEER_EXEMPT
_NON_SUBSTANTIVE_TOOLS = NON_SUBSTANTIVE_TOOLS
_DECISION_RESERVED_KEYS = DECISION_RESERVED_KEYS
