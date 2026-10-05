"""Compose existing JV Action tools into one Pydantic AI skill capability."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.tools import Tool as PydanticTool

from jvagent.action.orchestrator.pilot.contracts import PilotRunContext
from jvagent.action.orchestrator.skills import SkillDoc
from jvagent.harness.contracts import FORBIDDEN_AUTHORITY_KEYS
from jvagent.tooling.tool import Tool as JVTool

AUTHORITY_ARGUMENTS = (
    frozenset(
        {
            "agent_id",
            "user_id",
            "session_id",
            "caller_id",
            "task_id",
            "approved",
            "is_admin",
            "authorization",
        }
    )
    | FORBIDDEN_AUTHORITY_KEYS
)

logger = logging.getLogger(__name__)


def _contains_authority_field(value: Any) -> bool:
    """Reject reserved authority keys anywhere in model-provided arguments."""

    if isinstance(value, Mapping):
        return bool(AUTHORITY_ARGUMENTS.intersection(value)) or any(
            _contains_authority_field(item) for item in value.values()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_authority_field(item) for item in value)
    return False


AccessCheck = Callable[
    [PilotRunContext, str, str, Mapping[str, Any]], Awaitable[bool] | bool
]
ToolResultObserver = Callable[
    [PilotRunContext, str, Mapping[str, Any], str], Awaitable[None] | None
]
EffectInvoker = Callable[
    [
        PilotRunContext,
        str,
        Mapping[str, Any],
        Callable[[], Awaitable[bool]],
        Callable[[], Awaitable[Any]],
    ],
    Awaitable[Any],
]


class PilotToolConfigurationError(ValueError):
    """A skill cannot be safely composed from its configured Action tools."""


async def compose_skill_tools(
    skill: SkillDoc,
    action_tools: Sequence[tuple[str, JVTool]],
    *,
    run_context: PilotRunContext,
    access_check: AccessCheck,
    result_observer: ToolResultObserver | None = None,
    effect_invoker: EffectInvoker | None = None,
) -> tuple[PydanticTool[Any], ...]:
    """Validate declared dependencies and adapt only explicitly allowed tools.

    ``access_check`` must recheck current access for every execution. A missing
    or failing check is an error; loading a skill never grants authority.
    """

    if not callable(access_check):
        raise PilotToolConfigurationError("a server-side access check is required")
    permitted_names = tuple(dict.fromkeys(skill.requires_tools))
    if len(permitted_names) != len(skill.requires_tools):
        raise PilotToolConfigurationError("skill declares duplicate tool names")
    tools_by_name: dict[str, tuple[str, JVTool]] = {}
    for owner, tool in action_tools:
        if tool.name in tools_by_name:
            raise PilotToolConfigurationError(
                f"duplicate Action tool name: {tool.name}"
            )
        tools_by_name[tool.name] = (owner, tool)
    missing = [name for name in permitted_names if name not in tools_by_name]
    if missing:
        raise PilotToolConfigurationError(
            "skill requires unavailable Action tools: " + ", ".join(missing)
        )
    required_actions = set(skill.requires_actions)
    bindings: list[PydanticTool[Any]] = []
    for name in permitted_names:
        owner, original = tools_by_name[name]
        if required_actions and owner not in required_actions:
            raise PilotToolConfigurationError(
                f"tool {name!r} is not owned by a declared required Action"
            )
        if not isinstance(original.parameters_schema, dict):
            raise PilotToolConfigurationError(f"tool {name!r} has no object schema")
        schema_type = original.parameters_schema.get("type")
        if schema_type != "object":
            raise PilotToolConfigurationError(
                f"tool {name!r} must declare an object argument schema"
            )
        if original.idempotency_class is not None and effect_invoker is None:
            raise PilotToolConfigurationError(
                f"effect tool {name!r} requires a host effect invoker"
            )

        async def execute(
            ctx: RunContext[PilotRunContext],
            _original: JVTool = original,
            _tool_name: str = name,
            **kwargs: Any,
        ) -> str:
            logger.debug(
                "pilot Action dispatch",
                extra={
                    "pilot_correlation_id": ctx.deps.run_id,
                    "pilot_task_id": ctx.deps.task_id,
                    "pilot_skill_id": skill.name,
                    "pilot_tool_call_id": str(getattr(ctx, "tool_call_id", "") or ""),
                    "pilot_tool_name": _tool_name,
                },
            )
            if _contains_authority_field(kwargs):
                raise PilotToolConfigurationError(
                    "tool arguments cannot supply caller or approval authority"
                )
            allowed = access_check(ctx.deps, skill.name, _tool_name, kwargs)
            if inspect.isawaitable(allowed):
                allowed = await allowed
            if not allowed:
                raise PermissionError(
                    f"current caller is not allowed to invoke {_tool_name!r}"
                )

            async def recheck_access() -> bool:
                current = access_check(ctx.deps, skill.name, _tool_name, kwargs)
                if inspect.isawaitable(current):
                    current = await current
                return bool(current)

            async def invoke_action() -> Any:
                return await _original.call(**kwargs)

            if _original.idempotency_class is not None:
                assert effect_invoker is not None
                result = await effect_invoker(
                    ctx.deps,
                    _tool_name,
                    kwargs,
                    recheck_access,
                    invoke_action,
                )
            else:
                result = await invoke_action()
            if not hasattr(result, "is_error") or not hasattr(result, "content"):
                from jvagent.tooling.tool_result import ToolResult

                result = ToolResult(content=str(result))
            if result.is_error:
                # JV Actions communicate some failures through ToolResult instead
                # of raising. Do not pass those payloads to the model as successful
                # data or let the evidence observer treat them as receipts.
                raise RuntimeError(f"Action tool {_tool_name!r} returned an error")
            content = result.content
            result_limit = ctx.deps.max_tool_result_chars
            if len(content) > result_limit:
                marker = "\n[tool output truncated by pilot]"
                content = content[: result_limit - len(marker)] + marker
            if result_observer is not None:
                observed = result_observer(ctx.deps, _tool_name, kwargs, content)
                if inspect.isawaitable(observed):
                    await observed
            return content

        def validate_authority_fields(
            _ctx: RunContext[PilotRunContext], **args: Any
        ) -> None:
            if _contains_authority_field(args):
                raise ValueError(
                    "tool arguments cannot supply caller or approval authority"
                )

        bindings.append(
            PydanticTool.from_schema(
                execute,
                name=original.name,
                description=original.description,
                json_schema=original.parameters_schema,
                takes_ctx=True,
                args_validator=validate_authority_fields,
            )
        )
    return tuple(bindings)
