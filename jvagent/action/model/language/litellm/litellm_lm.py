"""LiteLLM-backed language model action (ADR-0045, model remediation Phase 2).

One action for every provider LiteLLM speaks. Model ids use LiteLLM's
``provider/model`` form (``openai/gpt-4o-mini``, ``anthropic/claude-sonnet-4-5``,
``ollama/llama3.1``, ``groq/llama-3.3-70b-versatile``, ``bedrock/...``); credentials
come from the provider's usual environment variables (``OPENAI_API_KEY``,
``ANTHROPIC_API_KEY``, ...) or the ``api_key`` / ``api_base`` attributes.

The wire format is LiteLLM's (OpenAI-shaped) so results map straight onto
``ModelActionResult`` and, through it, the normalised contract. Per-model
capabilities and pricing come from LiteLLM's metadata table
(``capabilities()`` / ``pricing()``), which is exactly what the capability
registry and cost estimator consume.

``litellm`` is an optional extra (``jvagent[litellm]``); it is imported lazily on
the first call so an install without it still boots, and the action reports a
clear error instead of an ImportError traceback.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.model.language.base import LanguageModelAction, ModelActionResult

logger = logging.getLogger(__name__)

_MISSING = (
    "LiteLLMLanguageModelAction needs the 'litellm' package: "
    "pip install 'jvagent[litellm]' (or litellm>=1.82.0)."
)


class LiteLLMLanguageModelAction(LanguageModelAction):
    """Universal adapter over ``litellm.acompletion``."""

    provider: str = attribute(default="litellm", description="Provider label")
    model: str = attribute(
        default="openai/gpt-4o-mini",
        description="LiteLLM model id in provider/model form.",
    )
    api_key: str = attribute(
        default="",
        description=(
            "Explicit API key for the target provider. Empty = LiteLLM reads the "
            "provider's own environment variable (OPENAI_API_KEY, "
            "ANTHROPIC_API_KEY, ...)."
        ),
    )
    api_base: str = attribute(
        default="",
        description="Override the provider base URL (Ollama host, Azure endpoint, proxy).",
    )
    drop_params: bool = attribute(
        default=True,
        description=(
            "Let LiteLLM drop request parameters the target provider does not "
            "support (e.g. parallel_tool_calls, response_format) instead of "
            "failing the call."
        ),
    )
    extra_params: Dict[str, Any] = attribute(
        default_factory=dict,
        description="Provider-specific parameters passed verbatim on every call.",
    )

    # -- litellm seam -----------------------------------------------------------

    @staticmethod
    def _litellm() -> Any:
        try:
            import litellm  # noqa: WPS433 - optional extra, imported lazily
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise RuntimeError(_MISSING) from exc
        return litellm

    async def _acompletion(self, **kwargs: Any) -> Any:
        """The single call into LiteLLM (patched by the conformance suite)."""
        return await self._litellm().acompletion(**kwargs)

    # -- request ----------------------------------------------------------------

    def _build_kwargs(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        stream: bool,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        model = kwargs.get("model") or self.model
        out: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "top_p": kwargs.get("top_p", self.top_p),
            "stream": bool(stream),
            "timeout": float(self.timeout),
            "drop_params": bool(self.drop_params),
            "num_retries": 0,  # the harness owns retries (BaseModelAction)
        }
        if stream:
            out["stream_options"] = {"include_usage": True}
        if tools:
            out["tools"] = tools
            if kwargs.get("tool_choice") is not None:
                out["tool_choice"] = kwargs["tool_choice"]
            if kwargs.get("parallel_tool_calls") is not None:
                out["parallel_tool_calls"] = bool(kwargs["parallel_tool_calls"])
        for key in ("response_format", "reasoning_effort", "thinking", "stop", "seed"):
            if kwargs.get(key) is not None:
                out[key] = kwargs[key]
        reasoning = kwargs.get("reasoning")
        if (
            isinstance(reasoning, dict)
            and reasoning.get("effort")
            and "reasoning_effort" not in out
        ):
            out["reasoning_effort"] = str(reasoning["effort"])
        if self.api_key:
            out["api_key"] = self.api_key
        if self.api_base:
            out["api_base"] = self.api_base
        if self.extra_params:
            out.update(dict(self.extra_params))
        return out

    # -- response mapping ---------------------------------------------------------

    @staticmethod
    def _dump(obj: Any) -> Any:
        if obj is None:
            return None
        if hasattr(obj, "model_dump"):
            try:
                return obj.model_dump()
            except Exception:  # pragma: no cover - defensive
                pass
        if isinstance(obj, dict):
            return obj
        return getattr(obj, "__dict__", obj)

    @classmethod
    def _usage_from(cls, usage: Any) -> Dict[str, Any]:
        raw = cls._dump(usage) or {}
        if not isinstance(raw, dict):
            return {}
        out: Dict[str, Any] = {
            "prompt_tokens": int(raw.get("prompt_tokens") or 0),
            "completion_tokens": int(raw.get("completion_tokens") or 0),
            "total_tokens": int(raw.get("total_tokens") or 0),
        }
        if not out["total_tokens"]:
            out["total_tokens"] = out["prompt_tokens"] + out["completion_tokens"]
        details = raw.get("prompt_tokens_details")
        details = cls._dump(details) if details is not None else None
        cached = 0
        if isinstance(details, dict) and details.get("cached_tokens"):
            cached = int(details.get("cached_tokens") or 0)
        cached = cached or int(raw.get("cache_read_input_tokens") or 0)
        if cached:
            out["cached_tokens"] = cached
        write = int(raw.get("cache_creation_input_tokens") or 0)
        if write:
            out["cache_creation_input_tokens"] = write
        comp_details = raw.get("completion_tokens_details")
        comp_details = cls._dump(comp_details) if comp_details is not None else None
        if isinstance(comp_details, dict) and comp_details.get("reasoning_tokens"):
            out["thinking_tokens"] = int(comp_details["reasoning_tokens"] or 0)
        return out

    @classmethod
    def _tool_calls_from(cls, message: Any) -> List[Dict[str, Any]]:
        calls = getattr(message, "tool_calls", None)
        if calls is None and isinstance(message, dict):
            calls = message.get("tool_calls")
        out: List[Dict[str, Any]] = []
        for call in calls or []:
            data = cls._dump(call) or {}
            fn = data.get("function") if isinstance(data, dict) else None
            fn = cls._dump(fn) or {}
            name = fn.get("name") if isinstance(fn, dict) else None
            if not name:
                continue
            arguments = fn.get("arguments")
            if isinstance(arguments, dict):
                arguments = json.dumps(arguments)
            out.append(
                {
                    "id": str(data.get("id") or ""),
                    "type": "function",
                    "function": {"name": str(name), "arguments": arguments or "{}"},
                }
            )
        return out

    @staticmethod
    def _thinking_from(message: Any) -> Optional[str]:
        for key in ("reasoning_content", "reasoning"):
            value = getattr(message, key, None)
            if value is None and isinstance(message, dict):
                value = message.get(key)
            if isinstance(value, str) and value.strip():
                return value
        extra = getattr(message, "provider_specific_fields", None)
        if isinstance(extra, dict):
            blocks = extra.get("thinking_blocks") or []
            parts = [
                str(b.get("thinking") or "")
                for b in blocks
                if isinstance(b, dict) and b.get("thinking")
            ]
            if parts:
                return "".join(parts)
        return None

    def _result_from_response(self, response: Any, model: str) -> ModelActionResult:
        choices = getattr(response, "choices", None) or []
        if not choices:
            raise RuntimeError("LiteLLM returned no choices")
        choice = choices[0]
        message = getattr(choice, "message", None)
        content = getattr(message, "content", None) if message is not None else None
        usage = self._usage_from(getattr(response, "usage", None))
        return ModelActionResult(
            response=content if isinstance(content, str) else "",
            usage=usage,
            model=str(getattr(response, "model", None) or model),
            provider=self.provider or "litellm",
            finish_reason=getattr(choice, "finish_reason", None),
            tool_calls=self._tool_calls_from(message),
            thinking_content=self._thinking_from(message),
            thinking_tokens=usage.get("thinking_tokens"),
        )

    # -- LanguageModelAction implementation -----------------------------------------

    async def _query(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> ModelActionResult:
        call = self._build_kwargs(messages, tools, stream=False, **kwargs)
        response = await self._acompletion(**call)
        return self._result_from_response(response, call["model"])

    async def _query_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> ModelActionResult:
        thinking_queue = kwargs.pop("_jv_thinking_queue", None)
        call = self._build_kwargs(messages, tools, stream=True, **kwargs)
        model = call["model"]
        result = ModelActionResult(
            usage={},
            model=model,
            provider=self.provider or "litellm",
            finish_reason=None,
            tool_calls=[],
            thinking_queue=thinking_queue,
        )

        async def generator() -> AsyncGenerator[str, None]:
            chunks: List[Any] = []
            try:
                stream = await self._acompletion(**call)
                async for chunk in stream:
                    chunks.append(chunk)
                    choices = getattr(chunk, "choices", None) or []
                    if not choices:
                        continue
                    delta = getattr(choices[0], "delta", None)
                    reasoning = (
                        self._thinking_from(delta) if delta is not None else None
                    )
                    if reasoning:
                        result.push_thinking_delta(reasoning)
                    text = (
                        getattr(delta, "content", None) if delta is not None else None
                    )
                    if isinstance(text, str) and text:
                        yield text
                    finish = getattr(choices[0], "finish_reason", None)
                    if finish:
                        result.finish_reason = finish
            finally:
                result.close_thinking_stream()
                if chunks:
                    try:
                        full = self._litellm().stream_chunk_builder(
                            chunks, messages=messages
                        )
                    except Exception as exc:  # pragma: no cover - defensive
                        logger.debug("litellm stream_chunk_builder failed: %s", exc)
                        full = None
                    if full is not None:
                        assembled = self._result_from_response(full, model)
                        result.tool_calls = assembled.tool_calls
                        if assembled.finish_reason:
                            result.finish_reason = assembled.finish_reason
                        if assembled.thinking_content:
                            result.thinking_content = assembled.thinking_content
                        if assembled.metrics.get("total_tokens"):
                            result.metrics.update(assembled.metrics)

        result.stream = generator()
        return result

    # -- metadata -----------------------------------------------------------------

    def capabilities(self, model: Optional[str] = None):
        from jvagent.action.model.capabilities import resolve_capabilities

        return resolve_capabilities(
            model or self.model,
            provider="litellm",
            overrides=getattr(self, "model_capabilities", None),
        )

    def pricing(self, model: Optional[str] = None):
        from jvagent.action.model.cost_estimator import pricing_for

        return pricing_for("litellm", model or self.model)


__all__ = ["LiteLLMLanguageModelAction"]
