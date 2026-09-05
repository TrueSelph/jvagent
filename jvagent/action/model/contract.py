"""The normalised model contract — the only model shape the harness consumes.

Phase 1 of the model-integration remediation
(``.planning/specs/2026-09-05-model-integration-remediation.md``): a small,
provider-neutral request/response vocabulary that the Orchestrator, ReplyAction
and every other model consumer read, with provider quirks normalised **here**
rather than at each call site.

- :class:`ModelRequest` — what a caller wants (OpenAI-shaped messages, tool
  definitions, tool-choice controls, response format, budgets, reasoning).
- :class:`ModelResponse` — what came back: ``text``, structured
  :class:`ToolCall` entries, a normalised :data:`FinishReason`, a normalised
  :class:`Usage` (prompt / completion / cached read / cached write / thinking),
  thinking text, model + provider labels, latency.
- :class:`ModelCapabilities` / :class:`Pricing` — per-model metadata. Populated
  from provider metadata in Phase 2; in Phase 1 every field defaults to
  "unknown" so nothing is guessed.
- :class:`ModelAdapter` — the protocol a provider integration satisfies.

Nothing in this module imports a provider. ``ModelResponse.from_result`` accepts
the legacy :class:`~jvagent.action.model.language.base.ModelActionResult` (and
any duck-typed stand-in tests use) so consumers can move to the contract while
adapters still produce the legacy object.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

# --------------------------------------------------------------------------- #
# Finish reasons
# --------------------------------------------------------------------------- #


class FinishReason:
    """Normalised completion reasons (plain strings, comparable across providers)."""

    STOP = "stop"
    TOOL_CALLS = "tool_calls"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"
    ERROR = "error"
    UNKNOWN = "unknown"


# Raw provider values → normalised reason. OpenAI-family: stop / length /
# tool_calls / function_call / content_filter. Anthropic: end_turn /
# stop_sequence / max_tokens / tool_use / refusal. Ollama: stop / length /
# load / unload. Vertex/Gemini via routers: STOP / MAX_TOKENS / SAFETY / RECITATION.
_FINISH_MAP: Dict[str, str] = {
    "stop": FinishReason.STOP,
    "end_turn": FinishReason.STOP,
    "stop_sequence": FinishReason.STOP,
    "completed": FinishReason.STOP,
    "eos": FinishReason.STOP,
    "tool_calls": FinishReason.TOOL_CALLS,
    "tool_use": FinishReason.TOOL_CALLS,
    "function_call": FinishReason.TOOL_CALLS,
    "length": FinishReason.LENGTH,
    "max_tokens": FinishReason.LENGTH,
    "max_output_tokens": FinishReason.LENGTH,
    "content_filter": FinishReason.CONTENT_FILTER,
    "refusal": FinishReason.CONTENT_FILTER,
    "safety": FinishReason.CONTENT_FILTER,
    "recitation": FinishReason.CONTENT_FILTER,
    "error": FinishReason.ERROR,
}


def normalize_finish_reason(raw: Any, *, has_tool_calls: bool = False) -> str:
    """Map a provider ``finish_reason`` / ``stop_reason`` onto :class:`FinishReason`.

    A response that carries tool calls is a ``tool_calls`` finish even when the
    provider labels it ``stop`` (Ollama) or omits the reason (some routers):
    what the model *did* outranks how the provider labelled it. An absent reason
    with no tool calls is ``stop`` — the common streaming case where the reason
    never arrived but the text did.
    """
    key = str(raw or "").strip().lower()
    mapped = _FINISH_MAP.get(key)
    if has_tool_calls and mapped in (None, FinishReason.STOP):
        return FinishReason.TOOL_CALLS
    if mapped is not None:
        return mapped
    if not key:
        return FinishReason.STOP
    return FinishReason.UNKNOWN


# --------------------------------------------------------------------------- #
# Tool calls
# --------------------------------------------------------------------------- #


@dataclass
class ToolCall:
    """One tool call the model made: provider id, tool name, parsed arguments.

    ``arguments`` is always a dict; a provider payload whose arguments string did
    not parse keeps the raw text in ``raw_arguments`` and an empty dict here, so
    a consumer can tell "no arguments" from "unparseable arguments".
    """

    id: str
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    raw_arguments: str = ""

    @classmethod
    def from_openai(cls, data: Any) -> Optional["ToolCall"]:
        """Build from an OpenAI-shaped ``tool_calls`` entry (the wire shape every
        adapter already emits, including Anthropic/Ollama after normalisation)."""
        if not isinstance(data, dict):
            return None
        fn = data.get("function") if isinstance(data.get("function"), dict) else {}
        name = str(fn.get("name") or data.get("name") or "").strip()
        if not name:
            return None
        raw = fn.get("arguments") if fn else data.get("arguments")
        arguments: Dict[str, Any] = {}
        raw_text = ""
        if isinstance(raw, dict):
            arguments = raw
        elif isinstance(raw, str):
            raw_text = raw
            text = raw.strip()
            if text:
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict):
                        arguments = parsed
                except json.JSONDecodeError:
                    pass
        return cls(
            id=str(data.get("id") or ""),
            name=name,
            arguments=arguments,
            raw_arguments=raw_text,
        )

    def to_openai(self) -> Dict[str, Any]:
        """The OpenAI-shaped ``tool_calls`` entry (what transcripts replay)."""
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }


# --------------------------------------------------------------------------- #
# Usage
# --------------------------------------------------------------------------- #


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


@dataclass
class Usage:
    """Token accounting with cache and thinking split out.

    ``prompt_tokens`` INCLUDES cached tokens (both OpenAI and Anthropic are
    folded to that convention by the adapters), so ``cached_read_tokens`` is a
    breakdown, never an addition. ``estimated`` marks usage the harness
    reconstructed with a tokenizer because the provider sent none (streaming).
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_read_tokens: int = 0
    cached_write_tokens: int = 0
    thinking_tokens: int = 0
    estimated: bool = False

    @classmethod
    def from_metrics(
        cls, metrics: Any, *, estimated: bool = False, thinking_tokens: Any = None
    ) -> "Usage":
        """Normalise a provider/legacy usage dict.

        Reads the flat keys the adapters already emit plus every cache spelling
        in use: OpenAI ``cached_tokens`` (flattened) or
        ``prompt_tokens_details.cached_tokens``; Anthropic
        ``cache_read_input_tokens`` / ``cache_creation_input_tokens``.
        """
        m = metrics if isinstance(metrics, dict) else {}
        prompt = _int(m.get("prompt_tokens"))
        completion = _int(m.get("completion_tokens"))
        total = _int(m.get("total_tokens")) or (prompt + completion)
        details = m.get("prompt_tokens_details")
        cached_read = _int(m.get("cached_tokens")) or _int(
            m.get("cache_read_input_tokens")
        )
        if not cached_read and isinstance(details, dict):
            cached_read = _int(details.get("cached_tokens"))
        cached_write = _int(m.get("cache_creation_input_tokens"))
        thinking = _int(thinking_tokens) or _int(m.get("thinking_tokens"))
        if not thinking:
            comp_details = m.get("completion_tokens_details")
            if isinstance(comp_details, dict):
                thinking = _int(comp_details.get("reasoning_tokens"))
        return cls(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
            cached_read_tokens=cached_read,
            cached_write_tokens=cached_write,
            thinking_tokens=thinking,
            estimated=bool(estimated),
        )


# --------------------------------------------------------------------------- #
# Request / response
# --------------------------------------------------------------------------- #


@dataclass
class ModelRequest:
    """A provider-neutral completion request.

    ``messages`` are OpenAI-shaped (``system`` / ``user`` / ``assistant`` with
    optional ``tool_calls`` / ``tool`` with ``tool_call_id``); adapters translate.
    ``tools`` are OpenAI function definitions. ``extra`` carries provider-specific
    passthrough (e.g. Anthropic ``thinking``) verbatim.
    """

    messages: List[Dict[str, Any]]
    model: Optional[str] = None
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Any] = None
    parallel_tool_calls: Optional[bool] = None
    response_format: Optional[Dict[str, Any]] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    reasoning: Optional[Dict[str, Any]] = None
    reasoning_effort: Optional[str] = None
    stream: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_query_kwargs(self) -> Dict[str, Any]:
        """Keyword arguments for ``LanguageModelAction.query_messages``.

        Only set fields travel, so an adapter's own defaults apply to the rest —
        the same shape callers already build by hand today.
        """
        kwargs: Dict[str, Any] = {
            "messages": list(self.messages),
            "stream": bool(self.stream),
            "tools": self.tools or None,
        }
        for key in (
            "model",
            "tool_choice",
            "parallel_tool_calls",
            "response_format",
            "max_tokens",
            "temperature",
            "top_p",
            "reasoning",
            "reasoning_effort",
        ):
            value = getattr(self, key)
            if value is not None:
                kwargs[key] = value
        kwargs.update(self.extra or {})
        return kwargs


@dataclass
class ModelResponse:
    """A normalised completion result — the only response shape consumers read."""

    text: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    finish_reason: str = FinishReason.STOP
    raw_finish_reason: Optional[str] = None
    usage: Usage = field(default_factory=Usage)
    thinking: str = ""
    model: str = ""
    provider: str = ""
    latency_ms: Optional[int] = None

    @property
    def truncated(self) -> bool:
        return self.finish_reason == FinishReason.LENGTH

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    def tool_calls_openai(self) -> List[Dict[str, Any]]:
        return [tc.to_openai() for tc in self.tool_calls]

    @classmethod
    def from_result(cls, result: Any) -> "ModelResponse":
        """Normalise a legacy ``ModelActionResult`` (or any duck-typed stand-in).

        Reads attributes defensively: a test double exposing only ``response``
        yields a text-only ``stop`` response.
        """
        if result is None:
            return cls()
        if isinstance(result, ModelResponse):
            return result
        text = getattr(result, "response", None)
        text = text if isinstance(text, str) else ""
        raw_calls = getattr(result, "tool_calls", None)
        calls: List[ToolCall] = []
        for item in raw_calls if isinstance(raw_calls, list) else []:
            call = ToolCall.from_openai(item)
            if call is not None:
                calls.append(call)
        raw_finish = getattr(result, "finish_reason", None)
        metrics = getattr(result, "metrics", None)
        duration = metrics.get("duration") if isinstance(metrics, dict) else None
        thinking = getattr(result, "thinking_content", None)
        return cls(
            text=text,
            tool_calls=calls,
            finish_reason=normalize_finish_reason(raw_finish, has_tool_calls=bool(calls)),
            raw_finish_reason=(
                str(raw_finish) if isinstance(raw_finish, str) and raw_finish else None
            ),
            usage=Usage.from_metrics(
                metrics,
                estimated=bool(getattr(result, "_usage_estimated", False)),
                thinking_tokens=getattr(result, "thinking_tokens", None),
            ),
            thinking=thinking if isinstance(thinking, str) else "",
            model=str(getattr(result, "model", "") or ""),
            provider=str(getattr(result, "provider", "") or ""),
            latency_ms=(
                int(float(duration) * 1000) if isinstance(duration, (int, float)) else None
            ),
        )


# --------------------------------------------------------------------------- #
# Metadata (Phase 2 populates; Phase 1 declares)
# --------------------------------------------------------------------------- #


@dataclass
class ModelCapabilities:
    """What a model can do. ``None`` means unknown — never guessed."""

    supports_tools: Optional[bool] = None
    supports_parallel_tools: Optional[bool] = None
    supports_json_mode: Optional[bool] = None
    supports_structured_output: Optional[bool] = None
    supports_vision: Optional[bool] = None
    supports_thinking: Optional[bool] = None
    context_window: Optional[int] = None
    max_output_tokens: Optional[int] = None
    source: str = "unknown"


@dataclass
class Pricing:
    """USD per million tokens, with cache multipliers relative to input."""

    input_per_million: float
    output_per_million: float
    cached_read_multiplier: float = 1.0
    cached_write_multiplier: float = 1.0
    source: str = "unknown"


@runtime_checkable
class ModelAdapter(Protocol):
    """What a provider integration exposes to the harness."""

    async def complete(self, request: ModelRequest) -> ModelResponse: ...

    def capabilities(self, model: Optional[str] = None) -> ModelCapabilities: ...

    def pricing(self, model: Optional[str] = None) -> Optional[Pricing]: ...


__all__ = [
    "FinishReason",
    "normalize_finish_reason",
    "ToolCall",
    "Usage",
    "ModelRequest",
    "ModelResponse",
    "ModelCapabilities",
    "Pricing",
    "ModelAdapter",
]
