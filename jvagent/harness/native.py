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
) -> Any:
    """Compose official Skills and native tools without a custom execution loop.

    Only reviewed read tools are admitted in this first increment. Writes, flows
    and visitor-bound tools require separate compatibility qualification.
    """
    from pydantic_ai import Agent, RunContext, Tool
    from pydantic_ai.capabilities import AbstractCapability, LocalWorkspace
    from pydantic_ai.exceptions import ModelRetry
    from pydantic_ai_harness import Skills

    directory = skill_directory.resolve(strict=True)
    metadata = admit_skill(
        directory,
        available_actions=available_actions,
        available_tools=[tool.name for tool in tools],
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

        async def before_run(self, ctx: RunContext[Any]) -> None:
            ctx.load_capability(metadata["name"])

    capabilities = [
        LocalWorkspace(directory.parent, read_only=True),
        Skills(".", include=[directory.name]),
        SelectedSkill(),
    ]
    if step_store is not None:
        from pydantic_ai_harness import StepPersistence

        capabilities.append(StepPersistence(store=step_store))
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
        capabilities.append(
            SpendLimits(
                store=spend_store,
                on_unpriced="raise",
                budgets=[
                    Budget(usd=run_budget, window="run"),
                    Budget(usd=conversation_budget, window="conversation"),
                ],
            )
        )
    return Agent(
        model,
        instructions=instructions,
        output_type=policy.finish if policy is not None else str,
        tools=admitted,
        capabilities=capabilities,
        name="jvagent-native",
    )


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
) -> Any:
    """Run through native lifecycle, limits and events, returning the full result.

    The host must persist typed messages and usage from this result and reconcile
    delivery separately. No partial text is substituted for a failed native run.
    """
    import math

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
    return await agent.run(
        question,
        conversation_id=conversation_id,
        run_id=run_id,
        message_history=message_history,
        event_stream_handler=event_stream_handler,
        usage_limits=UsageLimits(
            request_limit=max_requests,
            tool_calls_limit=max_tool_calls,
            total_tokens_limit=max_tokens,
        ),
        timeout=timeout_seconds,
    )
