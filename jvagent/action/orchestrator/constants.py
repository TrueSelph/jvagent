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
CONTRIB_TOOL_NAME_PREFIX = "contrib_"

# Positive allowlist for snake_case / namespaced tools that may emit directives.
# InteractAction class-name tools (PascalCase, no ``__``) remain trusted.
_TRUSTED_DIRECTIVE_EXACT = frozenset(
    {
        "reply",
        "use_skill",
        "find_tool",
        "clarify",
        "memory_get",
        "memory_set",
        "memory_append",
        "memory_search",
        "memory_delete",
    }
)
# Namespaced first-party tools (``ns__tool``) that legitimately emit
# directives. Kept generic here (no cross-subsystem literals); owning
# subsystems declare their own namespace via
# ``register_trusted_directive_prefix`` at load time (dependency inversion —
# the orchestrator carries no knowledge of specific plugins).
_TRUSTED_DIRECTIVE_PREFIXES_STATIC = ("orchestrator__",)
_TRUSTED_DIRECTIVE_PREFIXES_DYNAMIC: set = set()


def register_trusted_directive_prefix(prefix: str) -> None:
    """Declare a ``ns__`` tool namespace whose results may carry directives.

    Owning subsystems (e.g. a flow plugin whose tool results deliver
    ``next_tool`` / ``response_directive``) call this at load time so the
    orchestrator trusts them without hardcoding their names.
    """
    if prefix and str(prefix).strip():
        _TRUSTED_DIRECTIVE_PREFIXES_DYNAMIC.add(str(prefix).strip())


def is_untrusted_directive_source(tool_name: str) -> bool:
    """True if a raw result from *tool_name* must not be parsed for directives.

    Untrusted: ``mcp_*``, ``contrib_*``, and unknown ``ns__tool`` namespaces.
    Trusted: allowlisted core tools, registered first-party ``ns__`` namespaces,
    PascalCase IA tools, and simple first-party snake_case names (no ``__``).
    """
    if not tool_name:
        return False
    name = str(tool_name)
    if name.startswith(MCP_TOOL_NAME_PREFIX):
        return True
    if name.startswith(CONTRIB_TOOL_NAME_PREFIX):
        return True
    if name in _TRUSTED_DIRECTIVE_EXACT:
        return False
    if any(name.startswith(p) for p in _TRUSTED_DIRECTIVE_PREFIXES_STATIC):
        return False
    if any(name.startswith(p) for p in _TRUSTED_DIRECTIVE_PREFIXES_DYNAMIC):
        return False
    # Unknown namespaced tools (contrib packages often use ``pkg__tool``).
    if "__" in name:
        return True
    return False


# Backward-compatible aliases for tests and internal imports.
_TEXT_KEYS = TEXT_KEYS
_STEER_EXEMPT = STEER_EXEMPT
_NON_SUBSTANTIVE_TOOLS = NON_SUBSTANTIVE_TOOLS
_DECISION_RESERVED_KEYS = DECISION_RESERVED_KEYS
