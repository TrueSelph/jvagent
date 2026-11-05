"""Native-library composition for one explicitly selected JV skill.

Development entrypoint only: callers establish identity and provide an access
checker. This module does not import the legacy Orchestrator or pilot engine.
"""

from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from jvagent.harness.contracts import reject_model_authority_fields
from jvagent.harness.skill_admission import admit_skill
from jvagent.skills.output_policies import SUPPORTED_OUTPUT_CONTRACTS, output_policy
from jvagent.tooling.tool import Tool as JVTool

AccessCheck = Callable[[str], Awaitable[bool]]


def build_native_agent(
    model: Any,
    *,
    skill_directory: Path,
    tools: Sequence[JVTool],
    access_check: AccessCheck,
    instructions: str,
    step_store: Any = None,
    spend_store: Any = None,
    max_run_cost_usd: str = "2.00",
    max_conversation_cost_usd: str = "20.00",
    available_actions: Sequence[str] = (),
    channel: str = "default",
    delegates: Sequence[Any] = (),
    conversation_id: str | None = None,
    event_stream_handler: Any = None,
    spend_observer: Any = None,
    context_max_tokens: int = 32_000,
    context_keep_tokens: int = 16_000,
) -> Any:
    """Compose official Skills and native tools without a custom execution loop.

    Only reviewed read tools are admitted in this first increment. Writes, flows
    and visitor-bound tools require separate compatibility qualification.
    """
    from pydantic_ai import Agent, RunContext, Tool
    from pydantic_ai.capabilities import AbstractCapability, LocalWorkspace
    from pydantic_ai.exceptions import ModelRetry
    from pydantic_ai.workspaces import LocalWorkspaceBackend
    from pydantic_ai_harness import Skills

    if spend_observer is not None and spend_store is None:
        raise ValueError("A spend observer requires native spend tracking")
    for limit in (context_max_tokens, context_keep_tokens):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("Context limits must be positive integers")
    if context_keep_tokens >= context_max_tokens:
        raise ValueError("Context retention must be smaller than its trigger")
    directory = skill_directory.resolve(strict=True)
    metadata = admit_skill(
        directory,
        available_actions=available_actions,
        available_tools=[tool.name for tool in tools]
        + (["delegate_task"] if delegates else []),
        channel=channel,
        supported_output_contracts=SUPPORTED_OUTPUT_CONTRACTS,
    )
    if metadata["name"] != directory.name:
        raise ValueError(
            "Skill name must match its directory for official Skills loading"
        )
    policy = output_policy(metadata.get("output-contract", ""))
    allowed = metadata.get("allowed-tools", [])
    if len({tool.name for tool in tools}) != len(tools):
        raise ValueError("Duplicate Action tool names")

    def adapt(original: JVTool) -> Any:
        async def invoke(**arguments: Any) -> str:
            reject_model_authority_fields(arguments)
            # Bind the checked name in the closure, never in model arguments.
            if not await access_check(original.access_label or original.name):
                raise ModelRetry("Action access denied")
            result = await original.call(**arguments)
            if policy is not None:
                return await policy.observe(original.name, arguments, result)
            return str(result.content)

        return Tool.from_schema(
            invoke,
            name=original.name,
            description=original.description,
            json_schema=original.parameters_schema,
        )

    admitted = []
    for tool in tools:
        if tool.name not in allowed:
            continue
        if tool.effect_class != "read" or tool.binds_visitor:
            raise ValueError(
                f"Action is not qualified for native execution: {tool.name}"
            )
        admitted.append(adapt(tool))

    class SelectedSkill(AbstractCapability[Any]):
        """Materialize an explicitly configured skill using the library lifecycle."""

        id = "jv_selected_skill"

        async def wrap_run(self, ctx: RunContext[Any], *, handler: Any) -> Any:
            if policy is None:
                return await handler()
            with policy.run_scope():
                return await handler()

        async def before_run(self, ctx: RunContext[Any]) -> None:
            ctx.load_capability(metadata["name"])

        async def before_tool_execute(
            self,
            ctx: RunContext[Any],
            *,
            call: Any,
            tool_def: Any,
            args: dict[str, Any],
        ) -> dict[str, Any]:
            if call.tool_name == "delegate_task" and not await access_check(
                "delegate_task"
            ):
                raise ModelRetry("Delegation access denied")
            return args

    class ReceiptContext(AbstractCapability[Any]):
        """Bind telemetry for ordinary, delegated and library-summary requests."""

        async def wrap_model_request(
            self, ctx: RunContext[Any], *, request_context: Any, handler: Any
        ) -> Any:
            if spend_observer is None:
                return await handler(request_context)
            with spend_observer.bind(ctx):
                return await handler(request_context)

    run_services: list[Any] = [ReceiptContext()]
    if step_store is not None:
        from pydantic_ai_harness import StepPersistence

        from jvagent.harness.recovery import INPUT_CHECKPOINT_POLICY

        run_services.append(
            StepPersistence(
                store=step_store,
                capture_frontier=True,
                metadata={"jv_checkpoint_policy": INPUT_CHECKPOINT_POLICY},
            )
        )
    if spend_store is not None:
        from decimal import Decimal

        from pydantic_ai_harness.spend import Budget, SpendLimits

        run_budget = Decimal(max_run_cost_usd)
        conversation_budget = Decimal(max_conversation_cost_usd)
        if any(
            not amount.is_finite() or amount <= 0
            for amount in (run_budget, conversation_budget)
        ):
            raise ValueError("Spend ceilings must be finite and positive")
        run_services.append(
            SpendLimits(
                store=spend_store,
                on_unpriced="raise",
                on_spend=spend_observer.record if spend_observer is not None else None,
                budgets=[
                    Budget(usd=run_budget, window="run"),
                    Budget(usd=conversation_budget, window="conversation"),
                ],
            )
        )
    from pydantic_ai_harness.compaction import SummarizingCompaction

    capabilities = [
        LocalWorkspace(directory.parent, read_only=True),
        Skills(
            ".",
            include=[directory.name],
            workspace=LocalWorkspaceBackend(directory.parent),
        ),
        SelectedSkill(),
        *run_services,
        SummarizingCompaction(
            max_tokens=context_max_tokens,
            keep_tokens=context_keep_tokens,
            preserve_first_user_message=False,
            receipts=True,
            summarization_capabilities=run_services,
        ),
    ]
    if delegates:
        if len(delegates) > 16:
            raise ValueError("At most 16 named delegates may be configured")
        from pydantic import TypeAdapter
        from pydantic_ai_harness import SubAgent, SubAgents

        from jvagent.harness.delegation import ConversationAgent, NativeDelegate

        definitions = TypeAdapter(list[NativeDelegate]).validate_python(list(delegates))
        if not conversation_id or "delegate_task" not in allowed:
            raise ValueError("Delegation requires host scope and skill permission")
        if len({entry.name for entry in definitions}) != len(definitions):
            raise ValueError("Duplicate delegate names")
        if any(tool.name == "delegate_task" for tool in tools):
            raise ValueError("delegate_task is reserved for official SubAgents")
        children = []
        for entry in definitions:
            child = build_native_agent(
                model,
                skill_directory=Path(entry.skill_directory),
                tools=tools,
                access_check=access_check,
                instructions=instructions,
                step_store=step_store,
                spend_store=spend_store,
                max_run_cost_usd=max_run_cost_usd,
                max_conversation_cost_usd=max_conversation_cost_usd,
                available_actions=available_actions,
                channel=channel,
                spend_observer=spend_observer,
                context_max_tokens=context_max_tokens,
                context_keep_tokens=context_keep_tokens,
            )
            child.name = entry.name
            children.append(
                SubAgent(
                    ConversationAgent(child, conversation_id),
                    name=entry.name,
                    description=entry.description,
                    max_calls=entry.max_calls,
                    timeout_seconds=entry.timeout_seconds,
                    read_only=True,
                )
            )
        capabilities.append(
            SubAgents(
                agents=children,
                max_depth=2,
                forward_usage=True,
                event_stream_handler=event_stream_handler,
            )
        )
    agent = Agent(
        model,
        instructions=instructions,
        output_type=policy.finish if policy is not None else str,
        tools=admitted,
        capabilities=capabilities,
        name="jvagent-native",
    )
    return agent


async def run_native_agent(
    agent: Any,
    question: str,
    *,
    conversation_id: str,
    run_id: str,
    message_history: Sequence[Any] = (),
    event_stream_handler: Any = None,
    max_requests: int = 20,
    max_tool_calls: int = 40,
    max_tokens: int = 100_000,
    timeout_seconds: float = 180.0,
    max_cost_usd: str = "2.00",
) -> Any:
    """Run through native lifecycle, limits and events, returning the full result.

    The host must persist typed messages and usage from this result and reconcile
    delivery separately. No partial text is substituted for a failed native run.
    """
    import math
    from decimal import Decimal

    from pydantic_ai.usage import UsageLimits

    if not conversation_id.strip() or not run_id.strip():
        raise ValueError("Host-established conversation and run IDs are required")
    for limit in (max_requests, max_tool_calls, max_tokens):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("Native run limits must be positive integers")
    if (
        isinstance(timeout_seconds, bool)
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise ValueError("Native timeout must be finite and positive")
    cost_limit = Decimal(max_cost_usd)
    if not cost_limit.is_finite() or cost_limit <= 0:
        raise ValueError("Native cost limit must be finite and positive")
    return await agent.run(
        question,
        conversation_id=conversation_id,
        run_id=run_id,
        message_history=message_history,
        event_stream_handler=event_stream_handler,
        usage_limits=UsageLimits(
            cost_limit=cost_limit,
            request_limit=max_requests,
            tool_calls_limit=max_tool_calls,
            total_tokens_limit=max_tokens,
        ),
        timeout=timeout_seconds,
    )
