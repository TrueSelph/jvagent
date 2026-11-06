"""Reviewed mutation declarations; public SDK persistence owns execution."""

from typing import Any, ClassVar

from jvagent.tooling.context import require_tool_visitor
from jvagent.tooling.tool import Tool
from jvagent.tooling.tool_result import ToolResult

MUTATION_EFFECT_PREFIX = "jv:mutation:"
LEGACY_CONVERSATION_EFFECT = "jv:conversation-mutation:v1"


class MutationAccessDenied(PermissionError):
    """Host policy denied this mutation before its domain write was invoked."""


def is_mutation_effect(summary: str | None) -> bool:
    """Recognize retained conversation writes and new reviewed mutation facts."""
    return bool(summary) and (
        summary == LEGACY_CONVERSATION_EFFECT
        or summary.startswith(MUTATION_EFFECT_PREFIX)
    )


class NativeMutationTool(Tool):
    """Opt-in contract for reviewed writes, never automatic retry authority.

    Implementations own domain scope and durable acknowledgement. SDK execution
    must annotate the declared effect before calling this method. A failed run
    with any such effect cannot recover from its pre-write accepted input.
    """

    effect_summary: ClassVar[str] = ""

    async def call(self, **kwargs: Any) -> ToolResult:
        if kwargs.get("visitor") is not require_tool_visitor():
            raise ValueError("Mutation requires the admitted host visitor")
        result = await super().call(**kwargs)
        require_tool_visitor()
        if result.is_error:
            raise RuntimeError("Mutation did not complete successfully")
        return result
