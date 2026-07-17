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

# Directive-contract trust boundary (AUDIT-orchestrator HIGH).
# The next_tool / response_directive contract is a private control channel:
# a response_directive is delivered as the turn's reply bypassing the model,
# and a next_tool forces the loop to chain to a named tool. It must be honored
# only from server-generated framing or first-party tool results — NEVER from
# an MCP/third-party tool, whose output is external content in a multi-tenant
# deployment and could otherwise hijack egress or coerce tool-chaining.
# MCP tools are surfaced as ``mcp_{server}__{tool}`` (see mcp_action.get_tools).
MCP_TOOL_NAME_PREFIX = "mcp_"


def is_untrusted_directive_source(tool_name: str) -> bool:
    """True if a raw result from *tool_name* must not be parsed for directives."""
    return bool(tool_name) and str(tool_name).startswith(MCP_TOOL_NAME_PREFIX)


# Backward-compatible aliases for tests and internal imports.
_TEXT_KEYS = TEXT_KEYS
_STEER_EXEMPT = STEER_EXEMPT
_NON_SUBSTANTIVE_TOOLS = NON_SUBSTANTIVE_TOOLS
_DECISION_RESERVED_KEYS = DECISION_RESERVED_KEYS
