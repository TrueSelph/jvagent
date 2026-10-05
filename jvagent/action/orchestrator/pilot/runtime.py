"""Bridge the Pydantic AI execution loop to JV's configured model Action."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic_ai import Agent
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import (
    ModelMessage,
)
from pydantic_ai.messages import ModelRequest as PAIModelRequest
from pydantic_ai.messages import ModelResponse as PAIModelResponse
from pydantic_ai.messages import (
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage, UsageLimits

from jvagent.action.model.contract import ModelRequest, ModelResponse
from jvagent.action.orchestrator.pilot.contracts import (
    ConversationalReply,
    EvidenceReference,
    PilotOutput,
    PilotRunContext,
    ResearchBrief,
)
from jvagent.action.orchestrator.pilot.tools import (
    AccessCheck,
    EffectInvoker,
    ToolResultObserver,
    compose_skill_tools,
)
from jvagent.action.orchestrator.skills import SkillDoc
from jvagent.tooling.tool import Tool as JVTool


class PilotModelAdapterError(ValueError):
    """A message or model response is outside the pilot's supported text/tool subset."""

    finish_reason: str | None = None


class PilotEvidenceCollector:
    """Collect bounded references returned by successful read-tool calls."""

    max_references = 30
    max_excerpt_chars = 1600

    def __init__(self, references: Sequence[EvidenceReference] = ()) -> None:
        self._references = {ref.source_id: ref for ref in references}

    @staticmethod
    def _normalize_url(value: Any) -> str:
        raw = str(value or "").strip().rstrip(".,;:!?)]")
        try:
            parsed = urlsplit(raw)
        except ValueError:
            return ""
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))

    async def observe(
        self,
        _context: PilotRunContext,
        tool_name: str,
        args: dict[str, Any],
        content: str,
    ) -> None:
        """Record URLs and bounded excerpts from successful search/fetch results."""

        title = ""
        excerpt = content[: self.max_excerpt_chars]
        try:
            decoded = json.loads(content)
        except (TypeError, ValueError):
            decoded = None
        values: list[dict[str, Any]] = []
        if isinstance(decoded, list):
            values = [item for item in decoded if isinstance(item, dict)]
        elif isinstance(decoded, dict):
            values = [decoded]
        observed_urls = {
            self._normalize_url(match)
            for match in re.findall(r"https?://[^\s\"'<>]+", content)
        }
        requested_url = self._normalize_url(args.get("url"))
        if requested_url and tool_name.endswith(("__fetch", "__get")):
            observed_urls.add(requested_url)
        for item in values:
            item_url = self._normalize_url(item.get("link") or item.get("url"))
            if item_url:
                observed_urls.add(item_url)
                title = str(item.get("title") or title)[:512]
                snippet = str(item.get("snippet") or item.get("content") or "")
                if snippet:
                    excerpt = snippet[: self.max_excerpt_chars]
            identifier = str(item.get("id") or "").strip()
            if identifier:
                ref = EvidenceReference(
                    source_id=identifier,
                    url=item_url,
                    title=str(item.get("title") or "")[:512],
                    excerpt=excerpt,
                )
                if (
                    identifier in self._references
                    or len(self._references) < self.max_references
                ):
                    self._references[identifier] = ref
        for url in observed_urls:
            if url:
                ref = EvidenceReference(
                    source_id=url,
                    url=url,
                    title=title,
                    excerpt=excerpt,
                )
                if (
                    url in self._references
                    or len(self._references) < self.max_references
                ):
                    self._references[url] = ref

    def snapshot(self) -> tuple[EvidenceReference, ...]:
        """Return bounded references in deterministic order."""

        return tuple(self._references[key] for key in sorted(self._references))

    def validate(self, output: PilotOutput) -> None:
        """Require research citations to refer to an observed tool receipt."""

        if isinstance(output, ResearchBrief):
            known = set(self._references)
            missing = set(output.source_ids) - known
            if missing:
                raise PilotModelAdapterError(
                    "research output cited sources not returned by an Action: "
                    + ", ".join(sorted(missing)[:5])
                )
        elif not isinstance(output, ConversationalReply):
            raise PilotModelAdapterError("pilot returned an unsupported output type")


def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    raise PilotModelAdapterError("pilot currently supports text-only model messages")


def _to_jv_messages(
    messages: Sequence[ModelMessage], *, instructions: str | None
) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    if instructions:
        converted.append({"role": "system", "content": instructions})
    for message in messages:
        if isinstance(message, PAIModelRequest):
            for part in message.parts:
                if isinstance(part, SystemPromptPart):
                    converted.append(
                        {"role": "system", "content": _text_content(part.content)}
                    )
                elif isinstance(part, UserPromptPart):
                    converted.append(
                        {"role": "user", "content": _text_content(part.content)}
                    )
                elif isinstance(part, ToolReturnPart):
                    content = part.content
                    if not isinstance(content, str):
                        content = json.dumps(content, ensure_ascii=False, default=str)
                    converted.append(
                        {
                            "role": "tool",
                            "tool_call_id": part.tool_call_id,
                            "name": part.tool_name,
                            "content": content,
                        }
                    )
                elif isinstance(part, RetryPromptPart):
                    # Pydantic AI stores structured-output validation failures
                    # as ErrorDetails lists. ``model_response`` renders both
                    # those details and ordinary ModelRetry strings into the
                    # text the next model request should see.
                    content = part.model_response()
                    converted.append(
                        {
                            "role": "tool" if part.tool_call_id else "user",
                            "tool_call_id": part.tool_call_id,
                            "name": part.tool_name,
                            "content": content,
                        }
                    )
                else:
                    raise PilotModelAdapterError(
                        f"unsupported Pydantic AI request part: {type(part).__name__}"
                    )
        elif isinstance(message, PAIModelResponse):
            text = "".join(
                part.content for part in message.parts if isinstance(part, TextPart)
            )
            calls = [
                {
                    "id": part.tool_call_id,
                    "type": "function",
                    "function": {
                        "name": part.tool_name,
                        "arguments": (
                            part.args
                            if isinstance(part.args, str)
                            else json.dumps(part.args or {}, ensure_ascii=False)
                        ),
                    },
                }
                for part in message.parts
                if isinstance(part, ToolCallPart)
            ]
            if text or calls:
                entry: dict[str, Any] = {"role": "assistant", "content": text}
                if calls:
                    entry["tool_calls"] = calls
                converted.append(entry)
            if any(
                not isinstance(part, (TextPart, ToolCallPart)) for part in message.parts
            ):
                raise PilotModelAdapterError(
                    "pilot does not persist provider reasoning or multimodal parts"
                )
        else:
            raise PilotModelAdapterError(
                f"unsupported Pydantic AI message: {type(message).__name__}"
            )
    return converted


def _tool_definitions(info: AgentInfo) -> list[dict[str, Any]]:
    # AgentInfo's public properties expose only tools available for this run
    # step. ``model_request_parameters`` contains deferred tools as well, which
    # would bypass the capability activation boundary if sent to the provider.
    definitions = [*info.function_tools]
    definitions.extend(info.output_tools)
    return [
        {
            "type": "function",
            "function": {
                "name": definition.name,
                "description": definition.description or "",
                "parameters": definition.parameters_json_schema,
            },
        }
        for definition in definitions
    ]


def function_model_for_action(
    model_action: Any, *, model_id: str | None = None
) -> FunctionModel:
    """Adapt a configured JV ``LanguageModelAction`` to Pydantic AI's model API.

    Provider selection, credentials, retries, and transport remain owned by the
    existing Action. Unsupported message content fails explicitly rather than
    being silently flattened or dropped.
    """

    complete = getattr(model_action, "complete", None)
    if not callable(complete):
        raise TypeError("configured model Action must implement ModelAdapter.complete")

    async def request_model(
        messages: list[ModelMessage], info: AgentInfo
    ) -> PAIModelResponse:
        jv_request = ModelRequest(
            messages=_to_jv_messages(messages, instructions=info.instructions),
            model=model_id or str(getattr(model_action, "model", "") or "") or None,
            tools=_tool_definitions(info),
            max_tokens=(info.model_settings or {}).get("max_tokens"),
            temperature=(info.model_settings or {}).get("temperature"),
            top_p=(info.model_settings or {}).get("top_p"),
        )
        response = await complete(
            jv_request,
            calling_action_name="PydanticAICapabilityPilot",
        )
        if not isinstance(response, ModelResponse):
            raise PilotModelAdapterError("JV ModelAdapter returned an invalid response")
        parts: list[Any] = []
        if response.text:
            parts.append(TextPart(response.text))
        for call in response.tool_calls:
            if not call.id or not call.name:
                raise PilotModelAdapterError(
                    "JV model returned a tool call without identity"
                )
            parts.append(ToolCallPart(call.name, call.arguments, tool_call_id=call.id))
        if not parts:
            error = PilotModelAdapterError(
                "JV model returned neither text nor tool calls "
                f"(finish_reason={response.finish_reason}, "
                f"completion_tokens={response.usage.completion_tokens})"
            )
            error.finish_reason = response.finish_reason
            raise error
        return PAIModelResponse(
            parts=parts,
            model_name=response.model or None,
            usage=RequestUsage(
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
                cache_read_tokens=response.usage.cached_read_tokens,
                cache_write_tokens=response.usage.cached_write_tokens,
            ),
        )

    return FunctionModel(request_model, model_name="jvagent-language-model-action")


async def capability_for_skill(
    skill: SkillDoc,
    action_tools: Sequence[tuple[str, JVTool]],
    *,
    run_context: PilotRunContext,
    access_check: AccessCheck,
    result_observer: ToolResultObserver | None = None,
    effect_invoker: EffectInvoker | None = None,
) -> Capability[Any]:
    """Compile one existing SOP and its explicitly declared Action tools."""

    unsupported = list(skill.unsupported_features)
    if skill.task_lock or skill.requires_tasks:
        unsupported.extend(("task locking", "task prerequisites"))
    if skill.spec != "jv":
        unsupported.append("non-jv spec")
    if skill.extends:
        unsupported.append("skill inheritance")
    if skill.parameters:
        unsupported.append("skill parameters")
    if skill.lock_companions:
        unsupported.append("lock companions")
    if unsupported:
        raise PilotModelAdapterError(
            f"skill {skill.name!r} uses unsupported features: "
            + ", ".join(dict.fromkeys(unsupported))
        )
    if not skill.digest:
        raise PilotModelAdapterError(f"skill {skill.name!r} has no stable digest")
    if not skill.name:
        raise PilotModelAdapterError("skill name must be stable and non-empty")
    tools = await compose_skill_tools(
        skill,
        action_tools,
        run_context=run_context,
        access_check=access_check,
        result_observer=result_observer,
        effect_invoker=effect_invoker,
    )
    return Capability(
        id=skill.name,
        description=skill.description,
        instructions=skill.body,
        tools=tools,
        defer_loading=not skill.always_active,
    )


async def build_research_agent(
    model_action: Any,
    skills: Sequence[tuple[SkillDoc, Sequence[tuple[str, JVTool]]]],
    *,
    instructions: str,
    run_context: PilotRunContext,
    access_check: AccessCheck,
    result_observer: ToolResultObserver | None = None,
    effect_invoker: EffectInvoker | None = None,
    model_id: str | None = None,
    model_settings: dict[str, Any] | None = None,
) -> Agent[PilotRunContext, PilotOutput]:
    """Build one run-scoped agent from the existing skill and Action surface."""

    capabilities = [
        await capability_for_skill(
            skill,
            action_tools,
            run_context=run_context,
            access_check=access_check,
            result_observer=result_observer,
            effect_invoker=effect_invoker,
        )
        for skill, action_tools in skills
    ]
    names = [capability.id for capability in capabilities]
    if len(names) != len(set(names)):
        raise PilotModelAdapterError("skill names must be unique in a pilot run")
    # Pydantic AI 2.54's overload omits its public FunctionModel adapter in
    # static typing even though it is accepted at runtime.
    model: Any = function_model_for_action(model_action, model_id=model_id)
    return Agent(  # type: ignore[call-overload]
        model,
        instructions=instructions,
        output_type=PilotOutput,
        deps_type=PilotRunContext,
        model_settings=model_settings,
        capabilities=capabilities,
        retries=2,
        tool_timeout=45.0,
        max_concurrency=1,
        name="jvagent-skill-pilot",
    )


async def run_research_agent(
    agent: Agent[PilotRunContext, PilotOutput],
    question: str,
    *,
    run_context: PilotRunContext,
    evidence: PilotEvidenceCollector | None = None,
    message_history: Sequence[ModelMessage] | None = None,
) -> PilotOutput:
    """Run one bounded Pydantic AI loop and return validated pilot output."""

    result = await asyncio.wait_for(
        agent.run(
            question,
            message_history=message_history,
            deps=run_context,
            conversation_id=run_context.caller.session_id,
            run_id=run_context.run_id,
            usage_limits=UsageLimits(
                request_limit=run_context.max_model_requests,
                tool_calls_limit=run_context.max_tool_calls,
                total_tokens_limit=run_context.max_total_tokens,
                output_tokens_limit=run_context.max_output_tokens,
            ),
        ),
        timeout=run_context.max_runtime_seconds,
    )
    if not isinstance(result.output, (ResearchBrief, ConversationalReply)):
        raise PilotModelAdapterError("Pydantic AI returned an invalid pilot output")
    if evidence is not None:
        evidence.validate(result.output)
    return result.output
