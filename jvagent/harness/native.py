"""Native-library composition for JV skills and modular Action tools.

Development entrypoint only: callers establish identity and provide an access
checker. This module does not import the legacy Orchestrator or pilot engine.
"""

from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

from jvagent.harness.contracts import (
    OutputTokenLimitReached,
    reject_model_authority_fields,
)
from jvagent.harness.skill_catalog import NativeSkillCatalog
from jvagent.tooling.tool import Tool as JVTool

AccessCheck = Callable[[str], Awaitable[bool]]


def build_native_agent(
    model: Any,
    *,
    skill_directory: Path | None = None,
    skill_directories: Sequence[Path] = (),
    tools: Sequence[JVTool],
    toolsets: Sequence[Any] = (),
    action_skill_bases: Mapping[str, Path] | None = None,
    access_check: AccessCheck,
    instructions: str,
    tool_access_check: Callable[[JVTool], Awaitable[bool]] | None = None,
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
    model_settings: dict[str, Any] | None = None,
) -> Any:
    """Compose official Skills and native tools without a custom execution loop.

    Reviewed reads and explicit conversation-mutation adapters are admitted.
    Mutations require SDK persistence and host visitor scope. Other writes, flows
    and external effects require separate compatibility qualification.
    """
    from pydantic_ai import Agent, RunContext, Tool, ToolOutput
    from pydantic_ai.capabilities import AbstractCapability, LocalWorkspace
    from pydantic_ai.exceptions import ModelRetry
    from pydantic_ai_harness import Skills

    from jvagent.harness.conversation_tools import (
        CONVERSATION_EFFECT,
        ConversationMutationTool,
    )
    from jvagent.harness.model_settings import validate_model_settings
    from jvagent.harness.skill_assets import project_skill, projected_workspace

    settings = validate_model_settings(model_settings)

    if spend_observer is not None and spend_store is None:
        raise ValueError("A spend observer requires native spend tracking")
    for limit in (context_max_tokens, context_keep_tokens):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("Context limits must be positive integers")
    if context_keep_tokens >= context_max_tokens:
        raise ValueError("Context retention must be smaller than its trigger")
    catalog = NativeSkillCatalog(
        primary=skill_directory,
        directories=skill_directories,
        action_bases=action_skill_bases,
        available_actions=available_actions,
        available_tools=[tool.name for tool in tools]
        + [name for toolset in toolsets for name in toolset.declared_names]
        + (["delegate_task"] if delegates else []),
        channel=channel,
    )
    directory = catalog.directories[0]
    policy = catalog.policy
    allowed = catalog.allowed_names
    if len({tool.name for tool in tools}) != len(tools):
        raise ValueError("Duplicate Action tool names")

    def adapt(original: JVTool) -> Any:
        async def invoke(ctx: RunContext[Any], /, **arguments: Any) -> str:
            reject_model_authority_fields(arguments)
            if not catalog.allows_tool(ctx, original.name):
                raise ModelRetry("Load an authorized skill before calling this tool")
            catalog.note_tool(ctx, original.name, arguments)
            # Bind the checked name in the closure, never in model arguments.
            permitted = (
                await tool_access_check(original)
                if tool_access_check is not None
                else not original.requires_tool_permission
                and await access_check(original.access_label or original.name)
            )
            if not permitted:
                if isinstance(original, ConversationMutationTool):
                    raise PermissionError("Conversation mutation access denied")
                raise ModelRetry("Action access denied")
            call_arguments = dict(arguments)
            if original.binds_visitor:
                from jvagent.tooling.context import require_tool_visitor

                call_arguments["visitor"] = require_tool_visitor()
            if isinstance(original, ConversationMutationTool):
                from pydantic_ai.exceptions import ToolRetryError
                from pydantic_ai_harness.step_persistence import annotate_tool_effect

                await annotate_tool_effect(
                    step_store, ctx, effect_summary=CONVERSATION_EFFECT
                )
                effect = await step_store.get_tool_effect(
                    run_id=ctx.run_id, tool_call_id=ctx.tool_call_id
                )
                if effect is None or effect.effect_summary != CONVERSATION_EFFECT:
                    raise RuntimeError(
                        "Conversation mutation lacks an SDK effect record"
                    )
                try:
                    result = await original.call(**call_arguments)
                except (ModelRetry, ToolRetryError) as error:
                    raise RuntimeError(
                        "Conversation mutation requires reconciliation"
                    ) from error
            else:
                result = await original.call(**call_arguments)
            if result.is_error:
                raise ModelRetry(result.content)
            if policy is not None and catalog.requires_output_contract():
                return await policy.observe(original.name, arguments, result)
            return str(result.content)

        schema = dict(original.parameters_schema)
        if original.binds_visitor:
            properties = dict(schema.get("properties", {}))
            properties.pop("visitor", None)
            schema["properties"] = properties
            if "required" in schema:
                schema["required"] = [
                    key for key in schema["required"] if key != "visitor"
                ]
        return Tool.from_schema(
            invoke,
            name=original.name,
            description=original.description,
            json_schema=schema,
            takes_ctx=True,
            sequential=bool(original.binds_visitor),
        )

    declared = [tool.name for tool in tools] + [
        name for toolset in toolsets for name in toolset.declared_names
    ]
    if len(declared) != len(set(declared)):
        raise ValueError("Duplicate Action tool names")
    selected_toolsets = [
        toolset.for_skill(allowed, access_check, skill_check=catalog.allows_tool)
        for toolset in toolsets
        if toolset.declared_names.intersection(allowed)
    ]
    if policy is not None and any(
        toolset.declared_names.intersection(
            catalog.metadata[name].get("allowed-tools", [])
        )
        for name in catalog.contract_owners
        for toolset in selected_toolsets
    ):
        raise ValueError(
            "MCP evidence output contracts require provenance qualification"
        )
    admitted = []
    for tool in tools:
        if tool.name not in allowed:
            continue
        mutation = isinstance(tool, ConversationMutationTool)
        if mutation and (
            tool.effect_class != "write" or not tool.binds_visitor or step_store is None
        ):
            raise ValueError(
                "Conversation mutations require visitor scope and SDK persistence"
            )
        if (tool.effect_class != "read" and not mutation) or tool.terminal:
            raise ValueError(
                f"Action is not qualified for native execution: {tool.name}"
            )
        admitted.append(adapt(tool))

    def skill_library(path: Path) -> Any:
        _, asset = project_skill(path, action_bases=action_skill_bases)
        return Skills(
            ".", include=[path.name], workspace=projected_workspace(path, asset)
        )

    skill_libraries = list(catalog.libraries)
    preflight_children: list[Any] = []

    class SelectedSkill(AbstractCapability[Any]):
        """Materialize an explicitly configured skill using the library lifecycle."""

        id = "jv_selected_skill"

        async def wrap_run(self, ctx: RunContext[Any], *, handler: Any) -> Any:
            with catalog.run_scope():
                return await handler()

        async def before_run(self, ctx: RunContext[Any]) -> None:
            await catalog.preflight()
            for library in preflight_children:
                child_catalog = await library.load()
                if (
                    len(child_catalog.skills) != 1
                    or not child_catalog.skills[0].model_invocable
                ):
                    raise ValueError("Configured skill is not model-invocable: child")
            if catalog.primary is not None:
                ctx.load_capability(catalog.primary)

        async def prepare_tools(
            self, ctx: RunContext[Any], tool_defs: list[Any]
        ) -> list[Any]:
            return [
                tool
                for tool in tool_defs
                if tool.name not in allowed or catalog.allows_tool(ctx, tool.name)
            ]

        async def prepare_output_tools(
            self, ctx: RunContext[Any], tool_defs: list[Any]
        ) -> list[Any]:
            if catalog.primary is not None or catalog.requires_output_contract():
                return tool_defs
            return []

        async def after_output_process(
            self, ctx: RunContext[Any], *, output_context: Any, output: Any
        ) -> Any:
            if catalog.primary is None and catalog.requires_output_contract():
                if (
                    output_context.tool_call is None
                    or output_context.tool_call.tool_name != "finish"
                ):
                    raise ModelRetry(
                        "The loaded research skill requires its grounded finish output"
                    )
            return output

        async def before_tool_execute(
            self,
            ctx: RunContext[Any],
            *,
            call: Any,
            tool_def: Any,
            args: dict[str, Any],
        ) -> dict[str, Any]:
            if call.tool_name in allowed and not catalog.allows_tool(
                ctx, call.tool_name
            ):
                raise ModelRetry("Load an authorized skill before calling this tool")
            catalog.note_tool(ctx, call.tool_name, args)
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

    class CompleteOutput(AbstractCapability[Any]):
        """Reject a clipped final candidate after native request accounting."""

        async def before_output_process(
            self, ctx: RunContext[Any], *, output_context: Any, output: Any
        ) -> Any:
            from pydantic_ai.messages import ModelResponse

            if not ctx.partial_output:
                response = next(
                    (m for m in reversed(ctx.messages) if isinstance(m, ModelResponse)),
                    None,
                )
                if response is not None and response.finish_reason == "length":
                    raise OutputTokenLimitReached(
                        "Provider returned length-limited output"
                    )
            return output

    run_services: list[Any] = [ReceiptContext(), CompleteOutput()]
    if step_store is not None:
        from pydantic_ai_harness import StepPersistence

        from jvagent.harness.read_retries import ReadRetryReceipts
        from jvagent.harness.recovery import INPUT_CHECKPOINT_POLICY

        persistence = StepPersistence(
            store=step_store,
            capture_frontier=True,
            metadata={"jv_checkpoint_policy": INPUT_CHECKPOINT_POLICY},
        )
        run_services.append(persistence)
        run_services.append(
            ReadRetryReceipts(
                persistence,
                frozenset(
                    tool.name
                    for tool in tools
                    if tool.name in allowed and tool.effect_class == "read"
                ).union(
                    name
                    for toolset in selected_toolsets
                    for name in toolset.allowed_names
                ),
            )
        )
    if spend_store is not None:
        from pydantic_ai_harness.spend import SpendLimits

        from jvagent.harness.spend_store import native_budgets

        run_services.append(
            SpendLimits(
                store=spend_store,
                on_unpriced="raise",
                on_spend=spend_observer.record if spend_observer is not None else None,
                budgets=native_budgets(max_run_cost_usd, max_conversation_cost_usd),
            )
        )
    from pydantic_ai_harness.compaction import SummarizingCompaction

    capabilities = [
        LocalWorkspace(directory.parent, read_only=True),
        *skill_libraries,
        SelectedSkill(),
        # A failed summary is not an attempted parent request. Run compaction
        # before StepPersistence journals the parent's model-request start.
        SummarizingCompaction(
            model_settings=settings,
            max_tokens=context_max_tokens,
            keep_tokens=context_keep_tokens,
            preserve_first_user_message=False,
            receipts=True,
            summarization_capabilities=run_services,
        ),
        *run_services,
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
            preflight_children.append(
                skill_library(Path(entry.skill_directory).resolve(strict=True))
            )
            child = build_native_agent(
                model,
                skill_directory=Path(entry.skill_directory),
                action_skill_bases=action_skill_bases,
                tools=[
                    tool
                    for tool in tools
                    if not isinstance(tool, ConversationMutationTool)
                ],
                toolsets=toolsets,
                access_check=access_check,
                tool_access_check=tool_access_check,
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
                model_settings=settings,
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
        model_settings=settings,
        output_type=(
            [str, ToolOutput(policy.finish, name="finish")]
            if policy is not None and catalog.primary is None
            else policy.finish if policy is not None else str
        ),
        tools=admitted,
        toolsets=selected_toolsets,
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
    max_output_tokens: int | None = None,
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
    limits: tuple[int, ...] = (max_requests, max_tool_calls, max_tokens)
    if max_output_tokens is not None:
        limits += (max_output_tokens,)
    for limit in limits:
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
            output_tokens_limit=max_output_tokens,
        ),
        timeout=timeout_seconds,
    )
