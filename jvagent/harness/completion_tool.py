"""Host-bound model completion for native Action tools."""

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from jvagent.tooling.tool import Tool

CompleteText = Callable[[str], Awaitable[str]]


class NativeCompletionTool(Tool):
    """A reviewed read tool whose execute callable accepts host completion."""


def require_completion_receipts(tool: Tool, *services: Any) -> None:
    """Require the host's step and spend adapters for model-assisted tools."""
    if isinstance(tool, NativeCompletionTool) and any(
        service is None for service in services
    ):
        raise ValueError("Action completion requires native persistence and spend")


def completion_arguments(
    tool: Tool,
    ctx: Any,
    settings: dict[str, Any],
    capabilities: Sequence[Any],
) -> dict[str, CompleteText]:
    """Bind an isolated SDK completion to the authorized parent run."""
    if not isinstance(tool, NativeCompletionTool):
        return {}

    from jvspatial.core.context import (
        get_default_context,
        scoped_default_context_async,
    )
    from pydantic_ai import Agent

    graph = get_default_context()

    async def complete(prompt: str) -> str:
        if not ctx.conversation_id or ctx.usage_limits is None:
            raise RuntimeError("Action completion requires native run scope")
        child = Agent(
            ctx.model,
            name=tool.name,
            model_settings=settings,
            capabilities=capabilities,
        )
        # The integration may temporarily select a different service database.
        async with scoped_default_context_async(graph):
            result = await child.run(
                prompt,
                conversation_id=ctx.conversation_id,
                usage=ctx.usage,
                usage_limits=ctx.usage_limits,
            )
        return result.output

    return {"complete": complete}
