"""Terminal read receipts for a pinned upstream retry-control-flow gap."""

from typing import Any

from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import (
    ToolFailed,
    ToolFailedError,
    ToolRetryError,
    UnexpectedModelBehavior,
)

from jvagent.harness.mutation_tools import MutationOutcomeUnknown, is_mutation_effect


class ReadRetryReceipts(AbstractCapability[Any]):
    """Close known read/skill-load retries; never infer write rollback.

    Harness 0.66 StepPersistence starts an effect before execution, but core 2.66
    excludes retry control flow from on_tool_execute_error. Remove read receipt
    repair when upstream terminalizes these records; retain the regressions.
    The native factory also admits its official declarative Skills loader: an
    explicit retry rejection is known failure, not abandoned external work.
    Cancellation and storage failures still propagate without settlement.
    Annotated mutation control failures stop the run rather than authorize
    another attempt. Retain that host policy unless upstream supplies equivalent
    non-retriable effect semantics. SDK clocks and dispatch remain unchanged.
    """

    def __init__(
        self,
        persistence: Any,
        read_names: frozenset[str],
        pre_intent_mutations: frozenset[str] = frozenset(),
    ) -> None:
        self.persistence = persistence
        self.store = persistence.store
        self.read_names = read_names
        self.pre_intent_mutations = pre_intent_mutations

    async def wrap_tool_execute(
        self, ctx: RunContext[Any], *, call: Any, tool_def: Any, args: Any, handler: Any
    ) -> Any:
        try:
            return await handler(args)
        except (
            ModelRetry,
            ToolRetryError,
            ToolFailed,
            ToolFailedError,
            UnexpectedModelBehavior,
        ) as error:
            prior = await self.store.get_tool_effect(
                run_id=ctx.run_id, tool_call_id=call.tool_call_id
            )
            if prior is not None and is_mutation_effect(prior.effect_summary):
                raise MutationOutcomeUnknown(
                    "An annotated mutation cannot be retried after SDK execution failure"
                ) from error
            # NativeMutationTool cannot invoke its body before this annotation.
            # None proves admission has not accepted an intent; an arbitrary
            # failure summary is not evidence of rollback or non-execution.
            before_intent = (
                tool_def.name in self.pre_intent_mutations
                and prior is not None
                and prior.effect_summary is None
            )
            if tool_def.name in self.read_names or before_intent:
                if prior is not None and prior.status == "started":
                    # Delegate receipt/event semantics to the library's public
                    # error hook, which core skips for retry control flow.
                    return await self.persistence.on_tool_execute_error(
                        ctx, call=call, tool_def=tool_def, args=args, error=error
                    )
            raise
