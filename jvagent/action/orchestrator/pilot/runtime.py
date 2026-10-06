"""Bridge the Pydantic AI execution loop to JV's configured model Action."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import re
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timezone
from typing import Any

from pydantic_ai import Agent, ModelRetry, RunContext
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
from pydantic_ai.models.function import (
    AgentInfo,
    DeltaThinkingPart,
    DeltaToolCall,
    FunctionModel,
)
from pydantic_ai.usage import RequestUsage, UsageLimits

from jvagent.action.model.contract import FinishReason, ModelRequest, ModelResponse
from jvagent.action.orchestrator.pilot.contracts import (
    EvidenceReference,
    PilotOutput,
    PilotRunContext,
    ResearchBrief,
    normalize_evidence_url,
    output_user_text,
)
from jvagent.action.orchestrator.pilot.tools import (
    AccessCheck,
    EffectInvoker,
    ToolEventObserver,
    ToolResultObserver,
    compose_skill_tools,
)
from jvagent.action.orchestrator.skills import SkillDoc
from jvagent.tooling.tool import Tool as JVTool


class PilotModelAdapterError(ValueError):
    """A message or model response is outside the pilot's supported text/tool subset."""

    finish_reason: str | None = None


ReasoningObserver = Callable[[str], Awaitable[None] | None]
RequestGuard = Callable[[ModelRequest], Awaitable[None] | None]
ModelUsageObserver = Callable[[ModelResponse], Awaitable[None] | None]


class PilotBudgetExceeded(RuntimeError):
    """Host dollar policy stopped the pilot before another model request."""


class PilotEvidenceCollector:
    """Collect bounded references returned by successful read-tool calls."""

    max_references = 30
    max_excerpt_chars = 1600
    max_structured_result_chars = 256_000

    def __init__(self, references: Sequence[EvidenceReference] = ()) -> None:
        # Prior-run sources are deliberately not eligible in a new objective.
        # A follow-up must retrieve current evidence instead of inheriting a
        # stale, session-wide citation pool.
        self._references: dict[str, EvidenceReference] = {}
        self._fetch_aliases: dict[str, str] = {}
        self._overflow_urls: set[str] = set()
        self._overflow_fetch_requests: set[str] = set()

    @property
    def overflow_count(self) -> int:
        """Number of distinct source URLs withheld by the per-run cap."""
        return len(self._overflow_urls)

    @staticmethod
    def _normalize_url(value: Any) -> str:
        if not isinstance(value, str) or len(value) > 2048:
            return ""
        return normalize_evidence_url(value)

    @staticmethod
    def _source_id(identifier: Any, url: str) -> str:
        action_id = str(identifier or "").strip()
        if (
            action_id
            and len(action_id) <= 160
            and re.fullmatch(r"[A-Za-z0-9._:-]+", action_id)
        ):
            url_digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
            return f"action:{action_id}:{url_digest}"
        digest_input = f"{action_id}\0{url}" if action_id else url
        digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
        return ("result-sha256:" if action_id else "url-sha256:") + digest

    async def observe(
        self,
        _context: PilotRunContext,
        tool_name: str,
        args: dict[str, Any],
        content: str,
    ) -> None:
        """Record URLs and bounded excerpts from successful search/fetch results."""

        is_search = tool_name.endswith("__search")
        is_fetch = tool_name.endswith(("__fetch", "__get"))
        if not is_search and not is_fetch:
            return

        excerpt = content[: self.max_excerpt_chars]
        try:
            if len(content) > self.max_structured_result_chars:
                raise ValueError("structured result exceeds observation limit")
            decoded = json.loads(content)
        except (TypeError, ValueError):
            decoded = None
        values: list[dict[str, Any]] = []
        if isinstance(decoded, list):
            values = [item for item in decoded if isinstance(item, dict)]
        elif isinstance(decoded, dict):
            values = [decoded]
            for key in ("results", "organic", "organic_results"):
                nested = decoded.get(key)
                if isinstance(nested, list):
                    values = [item for item in nested if isinstance(item, dict)]
                    break

        # A search result's explicit link is a source receipt. URLs that happen
        # to appear in its snippet are not. Likewise, a fetched page proves
        # retrieval only for the requested URL; its body may mention links that
        # were never fetched and must not become citable evidence.
        requested_url = self._normalize_url(args.get("url"))
        for item in values:
            item_url = self._normalize_url(item.get("link") or item.get("url"))
            if not item_url or not is_search:
                continue
            source_id = self._source_id(item.get("id"), item_url)
            snippet = str(item.get("snippet") or item.get("content") or "")
            ref = EvidenceReference(
                source_id=source_id,
                url=item_url,
                title=str(item.get("title") or "")[:512],
                excerpt=(snippet or content)[: self.max_excerpt_chars],
                provenance="search_snippet",
                observed_at=datetime.now(timezone.utc),
            )
            if (
                source_id in self._references
                or len(self._references) < self.max_references
            ):
                self._references[source_id] = ref
            else:
                self._overflow_urls.add(item_url)

        # Fetch provenance comes from the Action's typed receipt, never text
        # markers that could be forged by a page body or another Action.
        tool_metadata = getattr(content, "tool_result_metadata", {})
        fetch_result = (
            tool_metadata.get("web_fetch_result", {})
            if isinstance(tool_metadata, dict)
            else {}
        )
        if is_fetch and isinstance(fetch_result, dict):
            final_url = self._normalize_url(fetch_result.get("final_url"))
            receipt_requested = self._normalize_url(fetch_result.get("requested_url"))
            content_type = str(fetch_result.get("content_type") or "").lower()
            source_line = next(
                (
                    line[len("# Source: ") :].strip()
                    for line in content.splitlines()[:5]
                    if line.startswith("# Source: ")
                ),
                "",
            )
            rendered_source_url = self._normalize_url(source_line)
            supported_content_type = not content_type or content_type in {
                "text/html",
                "application/xhtml",
                "application/xhtml+xml",
                "text/plain",
                "application/json",
                "text/markdown",
            }
            successful = (
                fetch_result.get("outcome") == "success"
                and fetch_result.get("http_status") == 200
                and receipt_requested == requested_url
                and bool(final_url)
                and rendered_source_url == final_url
                and supported_content_type
            )
            if successful:
                title_line = next(
                    (
                        line
                        for line in content.splitlines()[:5]
                        if line.startswith("# Title: ")
                    ),
                    "",
                )
                ref = EvidenceReference(
                    source_id=self._source_id("", final_url),
                    url=final_url,
                    title=title_line.removeprefix("# Title: ")[:512],
                    excerpt=excerpt[: self.max_excerpt_chars],
                    provenance="fetched_page",
                    observed_at=datetime.now(timezone.utc),
                )
                if (
                    ref.source_id in self._references
                    or len(self._references) < self.max_references
                ):
                    self._references[ref.source_id] = ref
                    self._fetch_aliases[requested_url] = ref.source_id
                else:
                    self._overflow_urls.add(final_url)
                    self._overflow_fetch_requests.add(requested_url)

    def snapshot(self) -> tuple[EvidenceReference, ...]:
        """Return bounded references in deterministic order."""

        return tuple(self._references[key] for key in sorted(self._references))

    def annotate_result(
        self,
        tool_name: str,
        args: dict[str, Any],
        content: str,
    ) -> str:
        """Expose host-assigned source IDs alongside full Action results."""
        if tool_name.endswith("__search"):
            if len(content) > self.max_structured_result_chars:
                return content
            try:
                decoded = json.loads(content)
            except (TypeError, ValueError):
                return content
            if isinstance(decoded, list):
                self._annotate_search_items(decoded)
            elif isinstance(decoded, dict):
                for key in ("results", "organic", "organic_results"):
                    nested = decoded.get(key)
                    if isinstance(nested, list):
                        self._annotate_search_items(nested)
                        break
                if self._overflow_urls:
                    decoded["pilot_evidence_limit_reached"] = True
                    decoded["pilot_evidence_omitted_count"] = self.overflow_count
            return json.dumps(decoded, ensure_ascii=False, separators=(",", ":"))
        elif tool_name.endswith(("__fetch", "__get")):
            requested = self._normalize_url(args.get("url"))
            if not requested:
                return self._sanitize_fetch_source_marker(content)
            content = self._sanitize_fetch_source_marker(content)
            reference = next(
                (
                    item
                    for source_id, item in self._references.items()
                    if self._fetch_aliases.get(requested) == source_id
                ),
                None,
            )
            if reference is not None:
                return content + f"\n\nObserved source ID: {reference.source_id}"
            if requested in self._overflow_fetch_requests:
                return (
                    content + "\n\nThis page was fetched but not retained as evidence: "
                    "the per-run source limit has been reached. Do not cite it."
                )
        return content

    @classmethod
    def _sanitize_fetch_source_marker(cls, content: str) -> str:
        """Remove URL credentials from the host-generated source header."""
        lines = content.splitlines()
        for index, line in enumerate(lines[:5]):
            if line.startswith("# Source: "):
                source_url = line[len("# Source: ") :].strip()
                normalized = cls._normalize_url(source_url)
                lines[index] = (
                    f"# Source: {normalized}"
                    if normalized
                    else "# Source: [unsafe URL removed]"
                )
                break
        return "\n".join(lines)

    def _annotate_search_items(self, values: list[Any]) -> None:
        for item in values:
            if not isinstance(item, dict):
                continue
            for key in ("link", "url"):
                raw_url = item.get(key)
                if not isinstance(raw_url, str):
                    continue
                safe_url = self._normalize_url(raw_url)
                if safe_url:
                    item[key] = safe_url
                else:
                    item.pop(key, None)
                    item["pilot_source_unavailable"] = (
                        "unsafe source URL removed; do not cite"
                    )
            url = self._normalize_url(item.get("link") or item.get("url"))
            reference = next(
                (
                    candidate
                    for candidate in self._references.values()
                    if candidate.url == url
                ),
                None,
            )
            if reference is not None:
                item["pilot_source_id"] = reference.source_id
            elif url in self._overflow_urls:
                item["pilot_source_unavailable"] = (
                    "per-run evidence source limit reached; do not cite"
                )

    def validate(self, output: PilotOutput) -> None:
        """Require each claim to quote a fresh, successfully fetched source."""

        if isinstance(output, ResearchBrief):
            known = set(self._references)
            requested = {
                source_id
                for finding in output.findings
                for source_id in finding.source_ids
            }
            missing = requested - known
            if missing:
                raise PilotModelAdapterError(
                    "research output cited sources not returned by an Action: "
                    + ", ".join(sorted(missing)[:5])
                )
            unusable = sorted(
                source_id
                for source_id in requested
                if not self._references[source_id].url
            )
            if unusable:
                raise PilotModelAdapterError(
                    "research output cited Action results without a source URL: "
                    + ", ".join(unusable[:5])
                )
            try:
                output_user_text(output, self.snapshot())
            except ValueError as exc:
                raise PilotModelAdapterError(str(exc)) from exc
        else:
            # The current capability pilot is the evidence-backed research
            # driver. Keep the legacy union only for reading prior snapshots;
            # never accept source-free output from a fresh research run.
            raise PilotModelAdapterError(
                "research pilot requires evidence-backed ResearchBrief output"
            )


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


def request_input_token_upper_bound(request: ModelRequest) -> int:
    """Return a conservative byte-derived prompt bound including tool schemas.

    This intentionally does not tokenize with a provider-specific tokenizer.
    UTF-8 request bytes plus framing headroom are used so schemas and tool
    arguments are not omitted from dollar-budget preflight.
    """
    messages = request.messages or []
    tools = request.tools or []
    payload = json.dumps(
        {
            "messages": messages,
            "tools": tools,
            "tool_choice": request.tool_choice,
            "response_format": request.response_format,
            "reasoning": request.reasoning,
            "reasoning_effort": request.reasoning_effort,
            "extra": request.extra,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    framing_headroom = 128 * (len(messages) + len(tools) + 1)
    return len(payload.encode("utf-8")) + framing_headroom


def function_model_for_action(
    model_action: Any,
    *,
    model_id: str | None = None,
    reasoning_observer: ReasoningObserver | None = None,
    request_guard: RequestGuard | None = None,
    usage_observer: ModelUsageObserver | None = None,
    request_overrides: dict[str, Any] | None = None,
) -> FunctionModel:
    """Adapt a configured JV ``LanguageModelAction`` to Pydantic AI's model API.

    Provider selection, credentials, retries, and transport remain owned by the
    existing Action. Unsupported message content fails explicitly rather than
    being silently flattened or dropped.
    """

    complete = getattr(model_action, "complete", None)
    if not callable(complete):
        raise TypeError("configured model Action must implement ModelAdapter.complete")

    def build_request(
        messages: list[ModelMessage], info: AgentInfo, *, stream: bool
    ) -> ModelRequest:
        settings = dict(info.model_settings or {})
        direct_settings = {
            key: settings.pop(key)
            for key in ("max_tokens", "temperature", "top_p")
            if key in settings
        }
        request_controls = {
            key: settings.pop(key)
            for key in (
                "tool_choice",
                "parallel_tool_calls",
                "response_format",
                "reasoning_effort",
                "reasoning",
            )
            if key in settings
        }
        extra_settings = dict(settings)
        supported_settings = set(
            getattr(model_action, "pydantic_ai_supported_settings", ()) or ()
        )
        # LiteLLM's drop_params mode can silently discard a provider-specific
        # setting. In strict mode, only use passthrough when the configured
        # Action opts out of that behavior; otherwise the setting would appear
        # accepted while having no effect.
        if getattr(model_action, "drop_params", False):
            supported_settings.clear()
        unsupported_settings = set(extra_settings) - supported_settings
        if unsupported_settings:
            raise PilotModelAdapterError(
                "configured JV model Action does not declare support for model "
                "settings: " + ", ".join(sorted(unsupported_settings))
            )
        if "stop_sequences" in extra_settings:
            extra_settings["stop"] = extra_settings.pop("stop_sequences")
        overrides = dict(request_overrides or {})
        unsupported_overrides = set(overrides) - {"reasoning", "reasoning_effort"}
        if unsupported_overrides:
            raise PilotModelAdapterError(
                "unsupported host model request overrides: "
                + ", ".join(sorted(unsupported_overrides))
            )
        reasoning = overrides.get("reasoning", request_controls.get("reasoning"))
        reasoning_effort = overrides.get(
            "reasoning_effort", request_controls.get("reasoning_effort")
        )
        jv_request = ModelRequest(
            messages=_to_jv_messages(messages, instructions=info.instructions),
            model=model_id or str(getattr(model_action, "model", "") or "") or None,
            tools=_tool_definitions(info),
            tool_choice=request_controls.get("tool_choice"),
            parallel_tool_calls=request_controls.get("parallel_tool_calls"),
            response_format=request_controls.get("response_format"),
            max_tokens=direct_settings.get("max_tokens"),
            temperature=direct_settings.get("temperature"),
            top_p=direct_settings.get("top_p"),
            reasoning=reasoning,
            reasoning_effort=reasoning_effort,
            stream=stream,
            extra=extra_settings,
        )
        return jv_request

    def response_parts(response: ModelResponse) -> list[Any]:
        parts: list[Any] = []
        if response.text:
            parts.append(TextPart(response.text))
        for call in response.tool_calls:
            if not call.id or not call.name:
                raise PilotModelAdapterError(
                    "JV model returned a tool call without identity"
                )
            arguments = call.arguments
            if call.raw_arguments:
                try:
                    raw_arguments = json.loads(call.raw_arguments)
                except (TypeError, json.JSONDecodeError) as exc:
                    raise PilotModelAdapterError(
                        f"JV model returned malformed tool arguments for {call.name}"
                    ) from exc
                if not isinstance(raw_arguments, dict):
                    raise PilotModelAdapterError(
                        f"JV model returned non-object tool arguments for {call.name}"
                    )
                if raw_arguments != call.arguments:
                    raise PilotModelAdapterError(
                        f"JV model tool arguments disagree with preserved raw arguments "
                        f"for {call.name}"
                    )
                arguments = raw_arguments
            parts.append(ToolCallPart(call.name, arguments, tool_call_id=call.id))
        return parts

    async def observe_response(
        response: ModelResponse, *, include_reasoning: bool = True
    ) -> None:
        if usage_observer is not None:
            observed_usage = usage_observer(response)
            if inspect.isawaitable(observed_usage):
                await observed_usage
        if (
            include_reasoning
            and reasoning_observer is not None
            and response.thinking.strip()
        ):
            observed = reasoning_observer(response.thinking)
            if inspect.isawaitable(observed):
                await observed

    def require_successful_finish(response: ModelResponse) -> None:
        if response.finish_reason in (
            FinishReason.LENGTH,
            FinishReason.CONTENT_FILTER,
            FinishReason.ERROR,
        ):
            error = PilotModelAdapterError(
                "JV model response did not complete successfully "
                f"(finish_reason={response.finish_reason}, "
                f"completion_tokens={response.usage.completion_tokens})"
            )
            error.finish_reason = response.finish_reason
            raise error

    async def request_model(
        messages: list[ModelMessage], info: AgentInfo
    ) -> PAIModelResponse:
        jv_request = build_request(messages, info, stream=False)
        if request_guard is not None:
            guarded = request_guard(jv_request)
            if inspect.isawaitable(guarded):
                await guarded
        response = await complete(
            jv_request,
            calling_action_name="PydanticAICapabilityPilot",
        )
        if not isinstance(response, ModelResponse):
            raise PilotModelAdapterError("JV ModelAdapter returned an invalid response")
        await observe_response(response)
        require_successful_finish(response)
        parts = response_parts(response)
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
            finish_reason=response.finish_reason,
            usage=RequestUsage(
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
                cache_read_tokens=response.usage.cached_read_tokens,
                cache_write_tokens=response.usage.cached_write_tokens,
            ),
        )

    async def stream_model(messages: list[ModelMessage], info: AgentInfo) -> Any:
        """Stream provider deltas through Pydantic AI without publishing drafts."""
        query_messages = getattr(model_action, "query_messages", None)
        if not callable(query_messages):
            # Third-party ModelAdapter implementations with only complete() keep
            # working; they do not claim live streaming support.
            response = await request_model(messages, info)
            tool_index = 0
            for part in response.parts:
                if isinstance(part, TextPart) and part.content:
                    yield part.content
                elif isinstance(part, ToolCallPart):
                    yield {
                        tool_index: DeltaToolCall(
                            name=part.tool_name,
                            json_args=(
                                part.args
                                if isinstance(part.args, str)
                                else json.dumps(part.args, ensure_ascii=False)
                            ),
                            tool_call_id=part.tool_call_id,
                        )
                    }
                    tool_index += 1
            return

        jv_request = build_request(messages, info, stream=True)
        if request_guard is not None:
            guarded = request_guard(jv_request)
            if inspect.isawaitable(guarded):
                await guarded
        result = await query_messages(
            **jv_request.to_query_kwargs(),
            calling_action_name="PydanticAICapabilityPilot",
        )
        if not getattr(result, "is_streaming", False):
            raise PilotModelAdapterError(
                "configured JV model Action did not provide a streaming response"
            )

        delta_queue: asyncio.Queue[tuple[str, str | None]] = asyncio.Queue()

        async def pump_text() -> None:
            try:
                async for text in result.iter_stream():
                    if text:
                        await delta_queue.put(("text", text))
            finally:
                await delta_queue.put(("done", None))

        async def pump_thinking() -> None:
            try:
                async for text in result.iter_thinking():
                    if text:
                        if reasoning_observer is not None:
                            observed = reasoning_observer(text)
                            if inspect.isawaitable(observed):
                                await observed
                        await delta_queue.put(("thinking", text))
            finally:
                await delta_queue.put(("done", None))

        pumps = [asyncio.create_task(pump_text()), asyncio.create_task(pump_thinking())]
        completed_pumps = 0
        try:
            while completed_pumps < len(pumps):
                kind, value = await delta_queue.get()
                if kind == "done":
                    completed_pumps += 1
                elif kind == "text" and value:
                    yield value
                elif kind == "thinking" and value:
                    # Keep the synthetic thinking part in a disjoint ID range
                    # from tool calls, whose provider IDs are zero-based indexes.
                    yield {-1: DeltaThinkingPart(content=value)}
            await asyncio.gather(*pumps)
        finally:
            for pump in pumps:
                if not pump.done():
                    pump.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)

        response = result.to_response()
        if not isinstance(response, ModelResponse):
            raise PilotModelAdapterError(
                "JV streaming Action returned an invalid response"
            )
        await observe_response(response, include_reasoning=False)
        require_successful_finish(response)
        for index, call in enumerate(response.tool_calls):
            part = response_parts(
                ModelResponse(
                    tool_calls=[call],
                    usage=response.usage,
                    finish_reason=response.finish_reason,
                )
            )[0]
            if not isinstance(part, ToolCallPart):
                continue
            yield {
                index: DeltaToolCall(
                    name=part.tool_name,
                    json_args=(
                        part.args
                        if isinstance(part.args, str)
                        else json.dumps(part.args, ensure_ascii=False)
                    ),
                    tool_call_id=part.tool_call_id,
                )
            }

    return FunctionModel(
        request_model,
        stream_function=stream_model,
        model_name="jvagent-language-model-action",
    )


async def capability_for_skill(
    skill: SkillDoc,
    action_tools: Sequence[tuple[str, JVTool]],
    *,
    run_context: PilotRunContext,
    access_check: AccessCheck,
    result_observer: ToolResultObserver | None = None,
    effect_invoker: EffectInvoker | None = None,
    tool_event_observer: ToolEventObserver | None = None,
    recoverable_tool_errors: frozenset[str] = frozenset(),
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
    if skill.output_contract != "evidence_required":
        raise PilotModelAdapterError(
            f"skill {skill.name!r} must declare output-contract: evidence_required"
        )
    if not skill.name:
        raise PilotModelAdapterError("skill name must be stable and non-empty")
    tools = await compose_skill_tools(
        skill,
        action_tools,
        run_context=run_context,
        access_check=access_check,
        result_observer=result_observer,
        effect_invoker=effect_invoker,
        tool_event_observer=tool_event_observer,
        recoverable_tool_errors=recoverable_tool_errors,
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
    tool_event_observer: ToolEventObserver | None = None,
    reasoning_observer: ReasoningObserver | None = None,
    request_guard: RequestGuard | None = None,
    usage_observer: ModelUsageObserver | None = None,
    request_overrides: dict[str, Any] | None = None,
    recoverable_tool_errors: frozenset[str] = frozenset(),
    tool_timeout_seconds: float | None = 45.0,
    max_tool_concurrency: int = 1,
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
            tool_event_observer=tool_event_observer,
            recoverable_tool_errors=recoverable_tool_errors,
        )
        for skill, action_tools in skills
    ]
    names = [capability.id for capability in capabilities]
    if len(names) != len(set(names)):
        raise PilotModelAdapterError("skill names must be unique in a pilot run")
    if not skills or any(
        skill.output_contract != "evidence_required" for skill, _action_tools in skills
    ):
        raise PilotModelAdapterError(
            "research pilot requires an evidence_required output contract"
        )
    normalized_tool_timeout: float | None = None
    if tool_timeout_seconds is not None:
        if isinstance(tool_timeout_seconds, bool) or not isinstance(
            tool_timeout_seconds, (int, float)
        ):
            raise PilotModelAdapterError(
                "tool timeout must be a finite non-negative number of seconds"
            )
        try:
            timeout_value = float(tool_timeout_seconds)
        except (OverflowError, ValueError) as exc:
            raise PilotModelAdapterError(
                "tool timeout must be a finite non-negative number of seconds"
            ) from exc
        if not math.isfinite(timeout_value) or timeout_value < 0:
            raise PilotModelAdapterError(
                "tool timeout must be a finite non-negative number of seconds"
            )
        normalized_tool_timeout = timeout_value or None
    if (
        isinstance(max_tool_concurrency, bool)
        or not isinstance(max_tool_concurrency, int)
        or not 1 <= max_tool_concurrency <= 8
    ):
        raise PilotModelAdapterError(
            "pilot tool concurrency must be an integer from 1 through 8"
        )
    # Pydantic AI 2.54's overload omits its public FunctionModel adapter in
    # static typing even though it is accepted at runtime.
    model: Any = function_model_for_action(
        model_action,
        model_id=model_id,
        reasoning_observer=reasoning_observer,
        request_guard=request_guard,
        usage_observer=usage_observer,
        request_overrides=request_overrides,
    )
    return Agent(  # type: ignore[call-overload]
        model,
        instructions=instructions,
        # Research skills cannot select the source-free conversational branch.
        output_type=ResearchBrief,
        deps_type=PilotRunContext,
        model_settings=model_settings,
        capabilities=capabilities,
        retries=2,
        tool_timeout=normalized_tool_timeout,
        max_concurrency=max_tool_concurrency,
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

    if evidence is not None:

        def validate_evidence_output(
            _ctx: RunContext[PilotRunContext], output: PilotOutput
        ) -> PilotOutput:
            try:
                evidence.validate(output)
            except PilotModelAdapterError as exc:
                raise ModelRetry(str(exc)) from exc
            return output

        agent.output_validator(validate_evidence_output)

    async def run_and_stream() -> Any:
        async with agent.run_stream_events(
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
        ) as events:
            async for event in events:
                # Consume the Pydantic AI event stream so the provider request
                # and tool calls complete under its normal lifecycle.
                del event
            result = events.result
            if result is None:
                raise PilotModelAdapterError(
                    "Pydantic AI stream ended without a completed run result"
                )
            return result

    result = await asyncio.wait_for(
        run_and_stream(), timeout=run_context.max_runtime_seconds
    )
    if not isinstance(result.output, ResearchBrief):
        raise PilotModelAdapterError(
            "Pydantic AI returned output outside the research skill contract"
        )
    if evidence is not None:
        evidence.validate(result.output)
    return result.output
