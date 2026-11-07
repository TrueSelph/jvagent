"""Host read policy around the public Pydantic MCP toolset and its lifecycle."""

from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, Iterable

from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.toolsets import ToolsetTool, WrapperToolset

from jvagent.harness.contracts import reject_model_authority_fields
from jvagent.tooling.context import require_tool_visitor


@dataclass
class ReviewedMCPToolset(WrapperToolset[Any]):
    """JV supplies scope/read admission; the SDK owns protocol and execution.

    Remote readOnlyHint annotations are advisory, never host authorization.
    This adapter remains necessary while JV owns authored tool permissions.
    """

    declared_names: frozenset[str]
    allowed_names: frozenset[str] = frozenset()
    access_check: Callable[[str], Awaitable[bool]] | None = None
    skill_check: Callable[[Any, str], bool] | None = None
    tool_timeout_seconds: float | None = None

    def for_skill(
        self,
        allowed: Iterable[str],
        access_check: Callable[[str], Awaitable[bool]],
        *,
        skill_check: Callable[[Any, str], bool] | None = None,
    ) -> "ReviewedMCPToolset":
        return replace(
            self,
            allowed_names=self.declared_names.intersection(allowed),
            access_check=access_check,
            skill_check=skill_check,
        )

    async def get_tools(self, ctx: RunContext[Any]) -> dict[str, ToolsetTool[Any]]:
        tools = await self.wrapped.get_tools(ctx)
        if self.declared_names - tools.keys():
            raise ValueError("native_read_tools names unavailable MCP tools")
        selected = {
            name: tool for name, tool in tools.items() if name in self.allowed_names
        }
        if any(
            (tool.tool_def.metadata or {}).get("task") for tool in selected.values()
        ):
            raise ValueError("Background MCP tools require separate qualification")
        return selected

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[Any],
        tool: ToolsetTool[Any],
    ) -> Any:
        require_tool_visitor()
        reject_model_authority_fields(tool_args)
        if (
            name not in self.allowed_names
            or (self.skill_check is not None and not self.skill_check(ctx, name))
            or self.access_check is None
            or not await self.access_check(name)
        ):
            raise ModelRetry("Action access denied")
        return await self.wrapped.call_tool(name, tool_args, ctx, tool)
