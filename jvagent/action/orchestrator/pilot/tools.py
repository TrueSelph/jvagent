"""Compose existing JV Action tools into one Pydantic AI skill capability."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
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
RESERVED_BINDING_ARGUMENTS = frozenset({"_tool_name", "_original"})

logger = logging.getLogger(__name__)


def _bound_tool_result(content: str, max_chars: int) -> str:
    """Bound Action context without returning a malformed JSON fragment."""
    if len(content) <= max_chars:
        return content
    if len(content) > 256_000:
        # Do not parse unbounded provider output merely to clip it. The
        # evidence collector has the same inspection ceiling; make the loss
        # explicit and return a valid bounded result envelope.
        notice = "tool output exceeded the pilot inspection limit"
        result = json.dumps(
            {"pilot_truncated": True, "notice": notice}, separators=(",", ":")
        )
        return result if len(result) <= max_chars else "[output too large]"
    marker = "\n[tool output truncated by pilot]"
    try:
        decoded = json.loads(content)
    except (TypeError, ValueError):
        citation_marker = "\n\nObserved source ID:"
        citation_at = content.rfind(citation_marker)
        suffix = content[citation_at:] if citation_at >= 0 else ""
        if suffix and len(suffix) < max_chars // 3:
            head_budget = max_chars - len(suffix) - len(marker)
            return content[:head_budget] + marker + suffix
        return content[: max_chars - len(marker)] + marker

    container: dict[str, Any] | None = None
    key: str | None = None
    if isinstance(decoded, list):
        rows = decoded
    elif isinstance(decoded, dict):
        container = decoded
        rows = []
        for candidate in ("results", "organic", "organic_results"):
            if isinstance(decoded.get(candidate), list):
                key = candidate
                rows = decoded[candidate]
                break
    else:
        rows = []

    if rows:
        candidates = [row.copy() if isinstance(row, dict) else row for row in rows]

        def render() -> str:
            if container is None:
                value: Any = candidates
            else:
                container[key or "results"] = candidates
                value = container
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

        # Keep citation IDs and links while trimming descriptive text first.
        for budget in (800, 400, 200, 100, 40, 0):
            for row in candidates:
                if not isinstance(row, dict):
                    continue
                for text_key in ("snippet", "content", "description", "summary"):
                    value = row.get(text_key)
                    if isinstance(value, str) and len(value) > budget:
                        row[text_key] = value[:budget]
            while candidates and len(render()) > max_chars:
                candidates.pop()
            if len(render()) <= max_chars:
                if container is not None:
                    container["pilot_truncated"] = True
                    result = json.dumps(
                        container, ensure_ascii=False, separators=(",", ":")
                    )
                    if len(result) <= max_chars:
                        return result
                    container.pop("pilot_truncated", None)
                return render()
        return json.dumps(
            {"pilot_truncated": True, "results": []}, separators=(",", ":")
        )

    low, high = 0, len(content)
    bounded_preview = json.dumps(
        {"pilot_truncated": True, "preview": ""},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    while low <= high:
        midpoint = (low + high) // 2
        candidate = json.dumps(
            {"pilot_truncated": True, "preview": content[:midpoint]},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(candidate) <= max_chars:
            bounded_preview = candidate
            low = midpoint + 1
        else:
            high = midpoint - 1
    return bounded_preview


def _contains_authority_field(value: Any) -> bool:
    """Reject authority and internal binding keys in model-provided arguments."""

    if isinstance(value, Mapping):
        return bool(
            (AUTHORITY_ARGUMENTS | RESERVED_BINDING_ARGUMENTS).intersection(value)
        ) or any(_contains_authority_field(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_authority_field(item) for item in value)
    return False


AccessCheck = Callable[
    [PilotRunContext, str, str, Mapping[str, Any]], Awaitable[bool] | bool
]
ToolResultObserver = Callable[
    [PilotRunContext, str, Mapping[str, Any], str], Awaitable[str | None] | str | None
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
ToolEventObserver = Callable[
    [str, PilotRunContext, str, str, Mapping[str, Any], Any],
    Awaitable[None] | None,
]


class PilotToolConfigurationError(ValueError):
    """A skill cannot be safely composed from its configured Action tools."""


@dataclass(frozen=True)
class PilotToolOutcome:
    """Typed event result; status is independent of user-visible text."""

    status: str
    content: str


async def compose_skill_tools(
    skill: SkillDoc,
    action_tools: Sequence[tuple[str, JVTool]],
    *,
    run_context: PilotRunContext,
    access_check: AccessCheck,
    result_observer: ToolResultObserver | None = None,
    effect_invoker: EffectInvoker | None = None,
    tool_event_observer: ToolEventObserver | None = None,
    recoverable_tool_errors: frozenset[str] = frozenset(),
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
    unexpected_recoverable = set(recoverable_tool_errors) - set(permitted_names)
    if unexpected_recoverable:
        raise PilotToolConfigurationError(
            "recoverable errors were configured for undeclared tools: "
            + ", ".join(sorted(unexpected_recoverable))
        )
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

    def bind_action(original: JVTool, name: str) -> PydanticTool[Any]:
        try:
            schema_validator = Draft202012Validator(original.parameters_schema)
            schema_validator.check_schema(original.parameters_schema)
        except Exception as exc:
            raise PilotToolConfigurationError(
                f"tool {name!r} has an invalid JSON Schema"
            ) from exc

        def validate_arguments(args: Mapping[str, Any]) -> None:
            error = next(iter(schema_validator.iter_errors(dict(args))), None)
            if error is not None:
                path = ".".join(str(part) for part in error.absolute_path)
                location = f" at {path}" if path else ""
                raise ValueError(f"invalid arguments for tool {name!r}{location}")

        async def execute(ctx: RunContext[PilotRunContext], **kwargs: Any) -> str:
            # Action identity and ACL label stay in this closure. Defaults on a
            # model-callable signature are model-overridable keyword arguments.
            call_id = str(getattr(ctx, "tool_call_id", "") or name)
            logger.debug(
                "pilot Action dispatch",
                extra={
                    "pilot_correlation_id": ctx.deps.run_id,
                    "pilot_task_id": ctx.deps.task_id,
                    "pilot_skill_id": skill.name,
                    "pilot_tool_call_id": str(getattr(ctx, "tool_call_id", "") or ""),
                    "pilot_tool_name": name,
                },
            )
            validate_arguments(kwargs)
            if _contains_authority_field(kwargs):
                raise PilotToolConfigurationError(
                    "tool arguments cannot supply reserved authority or binding fields"
                )
            allowed = access_check(ctx.deps, skill.name, name, kwargs)
            if inspect.isawaitable(allowed):
                allowed = await allowed
            if not allowed:
                raise PermissionError(
                    f"current caller is not allowed to invoke {name!r}"
                )
            if tool_event_observer is not None:
                observed = tool_event_observer(
                    "tool_call", ctx.deps, name, call_id, kwargs, None
                )
                if inspect.isawaitable(observed):
                    await observed

            async def recheck_access() -> bool:
                current = access_check(ctx.deps, skill.name, name, kwargs)
                if inspect.isawaitable(current):
                    current = await current
                return bool(current)

            async def invoke_action() -> Any:
                return await original.call(**kwargs)

            try:
                if original.idempotency_class is not None:
                    assert effect_invoker is not None
                    result = await effect_invoker(
                        ctx.deps,
                        name,
                        kwargs,
                        recheck_access,
                        invoke_action,
                    )
                else:
                    result = await invoke_action()
            except asyncio.CancelledError:
                if tool_event_observer is not None:
                    try:
                        observed = tool_event_observer(
                            "tool_result",
                            ctx.deps,
                            name,
                            call_id,
                            kwargs,
                            PilotToolOutcome("cancelled", "Action call was cancelled."),
                        )
                        if inspect.isawaitable(observed):
                            await asyncio.shield(observed)
                    except asyncio.CancelledError:
                        # Preserve the original cancellation after best-effort
                        # shielded event closure.
                        pass
                    except Exception:
                        logger.exception(
                            "failed to close pilot tool event after cancellation",
                            extra={
                                "pilot_tool_name": name,
                                "pilot_run_id": run_context.run_id,
                            },
                        )
                raise
            except Exception as exc:
                recoverable = name in recoverable_tool_errors
                status = (
                    "timed_out"
                    if isinstance(exc, (asyncio.TimeoutError, TimeoutError))
                    else "failed"
                )
                message = (
                    "The read Action timed out. No evidence was recorded. "
                    "Try another source or explain the limitation."
                    if status == "timed_out"
                    else "The read Action failed. No evidence was recorded. "
                    "Try another source or explain the limitation."
                )
                if recoverable:
                    logger.info(
                        "pilot read Action failed; returning a recoverable result",
                        extra={
                            "pilot_tool_name": name,
                            "pilot_run_id": ctx.deps.run_id,
                            "pilot_error_type": type(exc).__name__,
                        },
                    )
                if tool_event_observer is not None:
                    observed = tool_event_observer(
                        "tool_result",
                        ctx.deps,
                        name,
                        call_id,
                        kwargs,
                        PilotToolOutcome(status, message),
                    )
                    if inspect.isawaitable(observed):
                        await observed
                if recoverable:
                    return message
                raise
            if not hasattr(result, "is_error") or not hasattr(result, "content"):
                from jvagent.tooling.tool_result import ToolResult

                result = ToolResult(content=str(result))
            if result.is_error:
                # JV Actions communicate some failures through ToolResult instead
                # of raising. Do not pass those payloads to the model as successful
                # data or let the evidence observer treat them as receipts.
                error_text = (
                    "The read Action returned an error. No evidence was recorded. "
                    "Try another source or explain the limitation."
                )
                recoverable = name in recoverable_tool_errors
                if tool_event_observer is not None:
                    observed = tool_event_observer(
                        "tool_result",
                        ctx.deps,
                        name,
                        call_id,
                        kwargs,
                        PilotToolOutcome("failed", error_text),
                    )
                    if inspect.isawaitable(observed):
                        await observed
                if recoverable:
                    return error_text
                raise RuntimeError(f"Action tool {name!r} returned an error")
            content = result.content
            # Evidence provenance must be derived from the complete bounded
            # Action result. The model gets a separately clipped view below.
            if result_observer is not None:
                observer_content = content
                if result.metadata:
                    from jvagent.tooling.tool_result import ToolResultText

                    observer_content = ToolResultText(content, result.metadata)
                observed_content = result_observer(
                    ctx.deps, name, kwargs, observer_content
                )
                if inspect.isawaitable(observed_content):
                    observed_content = await observed_content
                if isinstance(observed_content, str):
                    content = observed_content
            result_limit = ctx.deps.max_tool_result_chars
            content = _bound_tool_result(content, result_limit)
            if tool_event_observer is not None:
                observed = tool_event_observer(
                    "tool_result", ctx.deps, name, call_id, kwargs, content
                )
                if inspect.isawaitable(observed):
                    await observed
            return content

        def validate_authority_fields(
            _ctx: RunContext[PilotRunContext], **args: Any
        ) -> None:
            validate_arguments(args)
            if _contains_authority_field(args):
                raise ValueError(
                    "tool arguments cannot supply reserved authority or binding fields"
                )

        return PydanticTool.from_schema(
            execute,
            name=original.name,
            description=original.description,
            json_schema=original.parameters_schema,
            takes_ctx=True,
            args_validator=validate_authority_fields,
        )

    for name in permitted_names:
        owner, original = tools_by_name[name]
        if name in recoverable_tool_errors and original.idempotency_class is not None:
            raise PilotToolConfigurationError(
                f"effect tool {name!r} cannot use recoverable read-error handling"
            )
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

        bindings.append(bind_action(original, name))
    return tuple(bindings)
