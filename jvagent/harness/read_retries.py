"""Terminal read receipts for a pinned upstream retry-control-flow gap."""

from typing import Any

from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ToolRetryError


class ReadRetryReceipts(AbstractCapability[Any]):
    """Close known read attempts; never retry tools or infer write rollback.

    Harness 0.66 StepPersistence starts an effect before execution, but core 2.66
    excludes retry control flow from on_tool_execute_error. Remove this adapter
    when upstream terminalizes these records; retain the protocol regressions.
    """

    def __init__(self, persistence: Any, read_names: frozenset[str]) -> None:
        self.persistence = persistence
        self.store = persistence.store
        self.read_names = read_names

    async def wrap_tool_execute(
        self, ctx: RunContext[Any], *, call: Any, tool_def: Any, args: Any, handler: Any
    ) -> Any:
        try:
            return await handler(args)
        except (ModelRetry, ToolRetryError) as error:
            if tool_def.name in self.read_names:
                prior = await self.store.get_tool_effect(
                    run_id=ctx.run_id, tool_call_id=call.tool_call_id
                )
                if prior is not None and prior.status == "started":
                    # Delegate receipt/event semantics to the library's public
                    # error hook, which core skips for retry control flow.
                    return await self.persistence.on_tool_execute_error(
                        ctx, call=call, tool_def=tool_def, args=args, error=error
                    )
            raise
