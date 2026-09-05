"""Shared orchestrator constants (loop + main action module)."""

from __future__ import annotations

import re
from typing import Any

# Keys the model commonly uses to carry user-facing text, in priority order.
TEXT_KEYS = ("answer", "text", "content", "message", "reply", "response")

# Egress + indirection tools are never "steered".
STEER_EXEMPT = frozenset(
    {"reply", "respond", "find_tool", "load_tool", "find_skill", "use_skill"}
)
NON_SUBSTANTIVE_TOOLS = STEER_EXEMPT

# Decision keys that are control/text fields, never tool arguments.
# Note: ``query`` is intentionally NOT reserved — it is a common real tool
# parameter (pageindex__search, find_tool, find_skill). Reserving it broke
# flattened model calls like {"tool":"pageindex__search","query":"..."}.
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
        # Native-protocol bookkeeping the loop strips before normalising
        # (see DECISION_META_KEYS) — reserved so a stray copy is never folded
        # into tool arguments.
        "_call_id",
        "_group_id",
        "_assistant_text",
    }
)

# Metadata ``_run_model`` attaches to a decision under the native protocol and
# the loop pops before ``_normalize``. ``_call_id`` / ``_group_id`` tie the
# resulting observation back to the provider's tool-call id (so the transcript
# replays as assistant ``tool_calls`` + ``tool`` results); ``_assistant_text``
# carries any prose the model emitted alongside (or instead of) a tool call.
DECISION_META_KEYS = frozenset({"_call_id", "_group_id", "_assistant_text"})

# Decision "actions" the loop treats as model-side faults rather than model
# choices. ``_run_model`` returns them instead of ``None`` so the loop can tell
# "the provider failed" (retry once, then end the turn with
# ``model_unavailable_text``) from "the model produced nothing usable" (nudge).
MODEL_ERROR_ACTION = "model_error"
MODEL_TRUNCATED_ACTION = "model_truncated"
MODEL_FAULT_ACTIONS = frozenset({MODEL_ERROR_ACTION, MODEL_TRUNCATED_ACTION})

# Tool-protocol names (``OrchestratorInteractAction.tool_protocol``).
TOOL_PROTOCOL_NATIVE = "native"
TOOL_PROTOCOL_JSON = "json"
# ``auto`` (default, ADR-0045): native unless the model's capabilities say it
# cannot call tools.
TOOL_PROTOCOL_AUTO = "auto"
TOOL_PROTOCOLS = frozenset(
    {TOOL_PROTOCOL_NATIVE, TOOL_PROTOCOL_JSON, TOOL_PROTOCOL_AUTO}
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


# --------------------------------------------------------------------------- #
# Task-lock result vocabulary (dependency inversion)
# --------------------------------------------------------------------------- #
#
# The loop detects two things in a task-lock skill's tool results without
# knowing which plugin produced them: that the task COMPLETED (so a blocked
# parent can resume in the same turn — ADR-0026 drain) and that an observation
# is the skill's ACTIVATION ENVELOPE (so prep re-grounding is not duplicated).
# The generic keys below are read always; a task-lock plugin whose envelope uses
# its own field names registers them at load time — the orchestrator carries no
# plugin literals (thin-harness invariants 6 and 8).
TASK_COMPLETION_FLAG = "task_complete"
TASK_LOCK_SKILL_KEY = "task_lock_skill"
_TASK_COMPLETION_FLAGS: set = {TASK_COMPLETION_FLAG}
_TASK_LOCK_SKILL_KEYS: set = {TASK_LOCK_SKILL_KEY}


def register_task_completion_flag(key: str) -> None:
    """Declare a result-envelope key whose truthy value marks task completion."""
    if key and str(key).strip():
        _TASK_COMPLETION_FLAGS.add(str(key).strip())


def register_task_lock_skill_key(key: str) -> None:
    """Declare a result-envelope key that names the owning task-lock skill."""
    if key and str(key).strip():
        _TASK_LOCK_SKILL_KEYS.add(str(key).strip())


def task_completion_flags() -> frozenset:
    return frozenset(_TASK_COMPLETION_FLAGS)


def task_lock_skill_keys() -> frozenset:
    return frozenset(_TASK_LOCK_SKILL_KEYS)


def is_task_completion(data: Any) -> bool:
    """True when a parsed tool-result envelope marks its task completed."""
    if not isinstance(data, dict):
        return False
    if data.get("status") == "completed":
        return True
    return any(bool(data.get(flag)) for flag in _TASK_COMPLETION_FLAGS)


# JSON Schema of a loop decision under the JSON protocol (ADR-0046 §structured
# decisions): sent as OpenAI ``response_format: json_schema`` or as a forced
# Anthropic tool so the provider validates the shape instead of prompt obedience.
DECISION_TOOL_NAME = "orchestrator_decision"
DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["tool", "final"],
            "description": "Call a tool, or finish the turn.",
        },
        "tool": {"type": "string", "description": "Exact tool name when action=tool."},
        "args": {
            "type": "object",
            "description": "Arguments for the tool when action=tool.",
        },
        "answer": {
            "type": "string",
            "description": "Optional closing text when action=final.",
        },
    },
    "required": ["action"],
}

# Loop outcome when a cost ceiling ends the turn (ADR-0046 §budget guard).
BUDGET_EXHAUSTED = "budget_exhausted"


# Backward-compatible aliases for tests and internal imports.
_TEXT_KEYS = TEXT_KEYS
_STEER_EXEMPT = STEER_EXEMPT
_NON_SUBSTANTIVE_TOOLS = NON_SUBSTANTIVE_TOOLS
_DECISION_RESERVED_KEYS = DECISION_RESERVED_KEYS


STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "you",
        "your",
        "are",
        "can",
        "could",
        "would",
        "want",
        "like",
        "need",
        "please",
        "this",
        "that",
        "have",
        "how",
        "what",
        "who",
        "when",
        "where",
        "why",
        "about",
        "into",
        "from",
        "get",
        "got",
        "tell",
        "let",
        "all",
        "any",
        "out",
        "use",
        "now",
    }
)


def significant_tokens(s: str) -> set:
    """Lowercase alnum tokens, len>2, minus stopwords — for relevance overlap."""
    return {
        w
        for w in re.findall(r"[a-z0-9]+", (s or "").lower())
        if len(w) > 2 and w not in STOPWORDS
    }
