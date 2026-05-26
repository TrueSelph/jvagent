"""OpenAI model action implementation.

Provides integration with OpenAI's Chat Completions API with support for
both synchronous and streaming responses. Supports multimodal queries
(text + images) for visual understanding.
"""

import json
import logging
import re
from typing import Any, AsyncGenerator, Dict, List, Optional, Union

import httpx
from jvspatial.core.annotations import attribute

from jvagent.action.model.language.base import (
    LanguageModelAction,
    ModelActionResult,
    ReasoningModelConfig,
)

logger = logging.getLogger(__name__)


def _log_openai_http_error(operation: str, exc: httpx.HTTPStatusError) -> None:
    """Log OpenAI error response body so 4xx failures are diagnosable from logs."""
    resp = exc.response
    snippet = ""
    try:
        snippet = (resp.text or "")[:4000]
    except Exception as read_err:  # pragma: no cover - defensive
        snippet = f"<failed to read body: {read_err}>"
    logger.error(
        "%s: OpenAI HTTP %s. Response body (truncated): %s",
        operation,
        resp.status_code,
        snippet or "(empty)",
    )


class OpenAILanguageModelAction(LanguageModelAction):
    """OpenAI language model integration action.

    Implements the LanguageModelAction interface for OpenAI's Chat Completions API.
    Supports all OpenAI models including GPT-4, GPT-3.5, and others.
    Supports multimodal queries (text + images) for visual understanding.

    Configuration:
        api_key: Optional override; otherwise OPENAI_API_KEY from the environment
        api_endpoint: API endpoint (defaults to https://api.openai.com/v1)
        model: Model identifier (e.g., 'gpt-4o-mini', 'gpt-4o', 'gpt-3.5-turbo')
        temperature: Sampling temperature
        max_tokens: Maximum tokens to generate
        top_p: Nucleus sampling parameter

    Examples:
        Programmatic usage:
        >>> action = await OpenAILanguageModelAction.get(action_id)
        >>> result = await action.query_sync("What is AI?")
        >>> response = await result.get_response()

        Streaming:
        >>> result = await action.query_stream("Tell me a story")
        >>> async for chunk in result.iter_stream():
        ...     print(chunk, end="")
    """

    # OpenAI-specific configuration
    api_endpoint: str = attribute(
        default="https://api.openai.com/v1", description="OpenAI API endpoint URL"
    )
    model: str = attribute(default="gpt-4o-mini", description="OpenAI model identifier")
    provider: str = attribute(default="openai", description="Provider name")

    reasoning_effort: Optional[str] = attribute(
        default=None,
        description=(
            "OpenAI Chat Completions reasoning effort for reasoning models: "
            "'minimal', 'low', 'medium', or 'high'."
        ),
    )
    reasoning_model_patterns: List[str] = attribute(
        default_factory=lambda: [
            r"^o1($|[-:])",
            r"^o3($|[-:])",
            r"^o4-mini",
            r"^gpt-5($|[-:.])",
        ],
        description="Regexes matched against model id to detect OpenAI reasoning models.",
    )
    is_reasoning_model: Optional[bool] = attribute(
        default=None,
        description=(
            "If True/False, force reasoning-model request shaping on/off; "
            "None = auto-detect from model id and reasoning_model_patterns."
        ),
    )

    # Pricing per 1M tokens (approximate, for cost estimation)
    _model_pricing: Dict[str, Dict[str, float]] = attribute(
        private=True,
        default_factory=lambda: {
            "gpt-4o": {"input": 2.50, "output": 10.00},
            "gpt-4o-mini": {"input": 0.150, "output": 0.600},
            "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
            "o1": {"input": 15.0, "output": 60.0},
            "o3-mini": {"input": 1.10, "output": 4.40},
            "o4-mini": {"input": 1.10, "output": 4.40},
            "gpt-5": {"input": 1.25, "output": 10.00},
        },
    )

    # ============================================================================
    # Lifecycle Hooks
    # ============================================================================

    async def on_register(self) -> None:
        """Called when action is registered during installation.

        Validates configuration. HTTP client initialization is handled
        by the base class. This method should only be called once during
        action registration.
        """
        await super().on_register()

        # Validate API key
        if not self._http_bearer_token():
            logger.warning(f"OpenAI action {self.label} has no API key configured")

    def translate_reasoning_config(self, cfg: ReasoningModelConfig) -> Dict[str, Any]:
        if cfg.profile == "final":
            return {}
        effort = cfg.reasoning_effort
        if effort is None and self.reasoning_effort is not None:
            effort = self.reasoning_effort
        if effort is None or str(effort).strip() == "":
            return {}

        reasoning_enabled = cfg.reasoning_enabled
        if reasoning_enabled is False:
            return {}
        if reasoning_enabled is True:
            return {"reasoning_effort": str(effort)}

        if self.matches_reasoning_model(self.model):
            return {"reasoning_effort": str(effort)}
        return {}

    def should_mirror_assistant_stream_as_thoughts(
        self, cfg: ReasoningModelConfig, **kwargs: Any
    ) -> bool:
        if cfg.mirror_assistant_stream_as_thoughts is not None:
            return bool(cfg.mirror_assistant_stream_as_thoughts)
        model_id = kwargs.get("model", self.model)
        return bool(self.matches_reasoning_model(model_id, **kwargs))

    def _http_bearer_token(self) -> str:
        """Bearer token for Authorization header (subclasses may change env fallbacks)."""
        return self.api_key_from_context("OPENAI_API_KEY")

    # ============================================================================
    # Query Implementation
    # ============================================================================

    @staticmethod
    def _normalize_reasoning_content(
        raw: Union[str, List[Any], Dict[str, Any], None],
    ) -> str:
        """Flatten API reasoning / chain-of-thought fields to a single string."""
        if raw is None or raw == "":
            return ""
        if isinstance(raw, str):
            return raw
        if isinstance(raw, dict):
            t = raw.get("text") or raw.get("content")
            return str(t) if t else ""
        if isinstance(raw, list):
            parts: List[str] = []
            for item in raw:
                if isinstance(item, dict):
                    t = item.get("text") or item.get("content")
                    if t:
                        parts.append(str(t))
                elif isinstance(item, str) and item:
                    parts.append(item)
            # Separate API list fragments with newlines so list-shaped reasoning
            # does not glue into one unreadable inline run.
            return "\n".join(s.strip() for s in parts if s and str(s).strip())
        return str(raw)

    def _detect_reasoning_model(self, model_id: str, **kwargs: Any) -> bool:
        """Whether to use OpenAI reasoning-model Chat Completions parameter shaping."""
        explicit = kwargs.get("is_reasoning_model")
        if explicit is not None:
            return bool(explicit)
        if self.is_reasoning_model is not None:
            return bool(self.is_reasoning_model)
        mid = model_id or ""
        for pattern in self.reasoning_model_patterns or []:
            try:
                if re.search(pattern, mid):
                    return True
            except re.error:
                logger.warning("Invalid reasoning_model_patterns regex: %s", pattern)
        return False

    def matches_reasoning_model(
        self, model_id: Optional[str] = None, **kwargs: Any
    ) -> bool:
        """True when request uses OpenAI reasoning-model parameter shaping."""
        mid = model_id if model_id is not None else kwargs.get("model", self.model)
        return self._detect_reasoning_model(mid, **kwargs)

    def _extract_reasoning_effort_for_payload(
        self, kwargs: Dict[str, Any]
    ) -> Optional[str]:
        """Resolve reasoning_effort for Chat Completions (top-level string)."""
        effort = kwargs.get("reasoning_effort")
        if effort is not None and str(effort).strip() != "":
            return str(effort)
        reasoning_cfg = kwargs.get("reasoning")
        if isinstance(reasoning_cfg, dict):
            e = reasoning_cfg.get("effort")
            if e is not None and str(e).strip() != "":
                return str(e)
        if (
            self.reasoning_effort is not None
            and str(self.reasoning_effort).strip() != ""
        ):
            return str(self.reasoning_effort)
        return None

    def _apply_reasoning_adjustments(
        self, payload: Dict[str, Any], kwargs: Dict[str, Any]
    ) -> None:
        """Mutate payload for OpenAI reasoning models: max_completion_tokens, reasoning_effort."""
        payload.pop("temperature", None)
        payload.pop("top_p", None)
        max_tok = payload.pop("max_tokens", None)
        if max_tok is not None:
            payload["max_completion_tokens"] = max_tok
        payload.pop("reasoning", None)
        effort = self._extract_reasoning_effort_for_payload(kwargs)
        if effort:
            payload["reasoning_effort"] = effort

    @staticmethod
    def _push_reasoning_usage_from_stream_chunk(
        result: ModelActionResult,
        usage: Dict[str, Any],
        emitted: List[bool],
    ) -> None:
        """Set ``thinking_tokens`` from usage when the stream reports reasoning_tokens.

        Does not push a visible thinking-delta; token counts are for metrics only.
        """
        if emitted[0]:
            return
        details = usage.get("completion_tokens_details")
        rt = None
        if isinstance(details, dict):
            rt = details.get("reasoning_tokens")
        if rt is None:
            rt = usage.get("reasoning_tokens")
        try:
            n = int(rt) if rt is not None else 0
        except (TypeError, ValueError):
            n = 0
        if n > 0:
            result.thinking_tokens = n
            emitted[0] = True

    def _build_openai_payload(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]],
        stream: bool,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Build JSON body for /chat/completions (sync or stream)."""
        model_override = kwargs.get("model", self.model)
        payload: Dict[str, Any] = {
            "model": model_override,
            "messages": messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "top_p": kwargs.get("top_p", self.top_p),
        }
        if stream:
            payload["stream"] = True
            stream_opts = dict(kwargs.get("stream_options") or {})
            stream_opts.setdefault("include_usage", True)
            payload["stream_options"] = stream_opts

        is_reasoning = self._detect_reasoning_model(model_override, **kwargs)
        reasoning_cfg = kwargs.get("reasoning")
        if reasoning_cfg is not None and not is_reasoning:
            payload["reasoning"] = reasoning_cfg

        if tools:
            payload["tools"] = tools

        if is_reasoning:
            self._apply_reasoning_adjustments(payload, kwargs)

        return payload

    async def _query(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> ModelActionResult:
        """Execute a synchronous query to OpenAI."""
        await self._initialize_http_client()
        extra_headers = kwargs.pop("_extra_headers", None)

        model_override = kwargs.get("model", self.model)
        payload = self._build_openai_payload(messages, tools, stream=False, **kwargs)

        # Make API request
        try:
            api_key = self._http_bearer_token()
            request_headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            if isinstance(extra_headers, dict):
                request_headers.update(extra_headers)
            response = await self._http_client.post(  # type: ignore[union-attr]
                f"{self.api_endpoint}/chat/completions",
                json=payload,
                headers=request_headers,
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                _log_openai_http_error("OpenAI chat/completions (sync)", e)
                raise
            data = response.json()

            # Extract response
            choice = data["choices"][0]
            message = choice["message"]
            content = message.get("content", "")
            finish_reason = choice.get("finish_reason")

            reasoning_raw = message.get("reasoning")
            if reasoning_raw is None:
                reasoning_raw = message.get("reasoning_content")
            thinking_content = self._normalize_reasoning_content(reasoning_raw) or None

            # Extract tool calls if present
            tool_calls = []
            if "tool_calls" in message:
                tool_calls = message["tool_calls"]

            # Extract usage and normalize to only include integer fields
            # OpenAI may include prompt_tokens_details and completion_tokens_details as dicts
            raw_usage = data.get("usage", {})
            usage = {
                "prompt_tokens": raw_usage.get("prompt_tokens", 0),
                "completion_tokens": raw_usage.get("completion_tokens", 0),
                "total_tokens": raw_usage.get("total_tokens", 0),
            }

            # Estimate cost (use raw_usage for cost calculation as it may have more details)
            self._estimate_cost(raw_usage, model_name=model_override)

            return ModelActionResult(
                response=content,
                usage=usage,
                model=model_override,  # Use the actual model used for this query
                # Read self.provider so subclasses (Groq, OpenRouter, …) that
                # override the attribute get the right label on the result.
                # Falls back to "openai" if a subclass forgot to set it.
                provider=getattr(self, "provider", None) or "openai",
                finish_reason=finish_reason,
                tool_calls=tool_calls,
                thinking_content=thinking_content,
            )

        except httpx.TimeoutException:
            raise
        except httpx.RequestError:
            raise
        except Exception as e:
            logger.error(f"OpenAI query failed: {e}", exc_info=True)
            raise

    async def _query_stream(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> ModelActionResult:
        """Execute a streaming query to OpenAI."""
        thinking_queue = kwargs.get("_jv_thinking_queue")
        await self._initialize_http_client()
        extra_headers = kwargs.pop("_extra_headers", None)

        model_override = kwargs.get("model", self.model)
        payload = self._build_openai_payload(messages, tools, stream=True, **kwargs)

        result = ModelActionResult(
            usage={},
            model=model_override,
            # Read self.provider so subclasses (Groq, OpenRouter, …) that
            # override the attribute get the right label on the result.
            provider=getattr(self, "provider", None) or "openai",
            finish_reason=None,
            tool_calls=[],
            thinking_content=None,
            thinking_tokens=None,
            thinking_queue=thinking_queue,
        )
        reasoning_accum: List[str] = []

        _REASONING_DELTA_EXACT_KEYS = frozenset(
            {
                "reasoning",
                "reasoning_content",
                "reasoning_summary_text",
                "reasoning_summary",
            }
        )

        def _delta_declares_reasoning(delta_obj: Dict[str, Any]) -> bool:
            """True when this chunk's delta carries reasoning fields (vs message-only)."""
            for k in delta_obj:
                if k in _REASONING_DELTA_EXACT_KEYS or "reasoning" in k.lower():
                    return True
            return False

        def append_reasoning_delta(raw: Any) -> None:
            if raw is None or raw == "":
                return
            if isinstance(raw, str):
                reasoning_accum.append(raw)
                result.push_thinking_delta(raw)
                return
            normalized = self._normalize_reasoning_content(raw)
            if normalized:
                reasoning_accum.append(normalized)
                result.push_thinking_delta(normalized)

        def _finalize_stream_tool_calls(
            buffers: Dict[int, Dict[str, Any]],
        ) -> List[Dict[str, Any]]:
            """Turn per-index OpenAI streaming buffers into OpenAI tool_calls list."""
            if not buffers:
                return []
            out: List[Dict[str, Any]] = []
            for idx in sorted(buffers.keys()):
                buf = buffers[idx]
                name = buf.get("name") or ""
                if not name:
                    continue
                tc_id = buf.get("id") or ""
                args = buf.get("arguments") or ""
                if not isinstance(args, str):
                    args = json.dumps(args) if args else "{}"
                out.append(
                    {
                        "id": tc_id,
                        "type": "function",
                        "function": {"name": name, "arguments": args},
                    }
                )
            return out

        # Create streaming generator
        async def stream_generator() -> AsyncGenerator[str, None]:
            """Generate streaming chunks from OpenAI API."""
            finish_reason = None
            accumulated_chunks = []
            tool_call_buffers: Dict[int, Dict[str, Any]] = {}
            usage_reasoning_emitted: List[bool] = [False]

            try:
                api_key = self._http_bearer_token()
                request_headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }
                if isinstance(extra_headers, dict):
                    request_headers.update(extra_headers)
                async with self._http_client.stream(  # type: ignore[union-attr]
                    "POST",
                    f"{self.api_endpoint}/chat/completions",
                    json=payload,
                    headers=request_headers,
                ) as response:
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as e:
                        await response.aread()
                        _log_openai_http_error("OpenAI chat/completions (stream)", e)
                        raise

                    # Parse SSE stream
                    async for line in response.aiter_lines():
                        if not line or line.startswith(":"):
                            continue

                        if line.startswith("data: "):
                            data_str = line[6:]  # Remove "data: " prefix

                            if data_str == "[DONE]":
                                break

                            try:
                                data = json.loads(data_str)
                                usage_blob = data.get("usage")
                                if isinstance(usage_blob, dict):
                                    self._push_reasoning_usage_from_stream_chunk(
                                        result,
                                        usage_blob,
                                        usage_reasoning_emitted,
                                    )

                                choices = data.get("choices") or []
                                if not choices:
                                    continue

                                choice = choices[0]
                                delta = choice.get("delta", {}) or {}

                                msg = choice.get("message")
                                if isinstance(msg, dict):
                                    r_msg = msg.get("reasoning")
                                    if r_msg is None:
                                        r_msg = msg.get("reasoning_content")
                                    # Avoid duplicating reasoning when the same chunk
                                    # includes both message.* and delta.reasoning* fields.
                                    if (
                                        r_msg is not None
                                        and not _delta_declares_reasoning(delta)
                                    ):
                                        append_reasoning_delta(r_msg)

                                for key, val in delta.items():
                                    if key in (
                                        "content",
                                        "role",
                                        "tool_calls",
                                        "function_call",
                                        "refusal",
                                        "obfuscation",
                                    ):
                                        continue
                                    # Handled explicitly below to avoid double-appending.
                                    if key in _REASONING_DELTA_EXACT_KEYS:
                                        continue
                                    if (
                                        "reasoning" in key.lower()
                                        and isinstance(val, str)
                                        and val.strip()
                                    ):
                                        append_reasoning_delta(val)

                                r_raw = delta.get("reasoning")
                                if r_raw is None:
                                    r_raw = delta.get("reasoning_content")
                                append_reasoning_delta(r_raw)

                                r_sum = delta.get("reasoning_summary_text")
                                if r_sum is None:
                                    r_sum = delta.get("reasoning_summary")
                                append_reasoning_delta(r_sum)

                                # Extract content delta (after reasoning so queue sees CoT first)
                                content = delta.get("content", "") or ""

                                if content:
                                    accumulated_chunks.append(content)
                                    yield content

                                # Merge streaming tool call fragments by index
                                for tc_delta in delta.get("tool_calls") or []:
                                    if not isinstance(tc_delta, dict):
                                        continue
                                    idx = tc_delta.get("index")
                                    if idx is None:
                                        continue
                                    if idx not in tool_call_buffers:
                                        tool_call_buffers[idx] = {
                                            "id": None,
                                            "name": None,
                                            "arguments": "",
                                        }
                                    buf = tool_call_buffers[idx]
                                    if tc_delta.get("id"):
                                        buf["id"] = tc_delta["id"]
                                    fn = tc_delta.get("function")
                                    if isinstance(fn, dict):
                                        if fn.get("name"):
                                            buf["name"] = fn["name"]
                                        arg_part = fn.get("arguments")
                                        if arg_part:
                                            buf["arguments"] += (
                                                arg_part
                                                if isinstance(arg_part, str)
                                                else json.dumps(arg_part)
                                            )

                                # Track finish reason
                                if "finish_reason" in choice:
                                    finish_reason = choice["finish_reason"]
                                    result.finish_reason = finish_reason

                                # OpenAI doesn't provide usage in stream by default
                                # Token estimation will be handled by the stream wrapper in base.py

                            except json.JSONDecodeError:
                                logger.warning(f"Failed to parse SSE data: {data_str}")
                                continue

            except httpx.HTTPStatusError:
                # Re-raise immediately - let the error handler log and format the response
                raise
            except httpx.TimeoutException:
                raise
            except httpx.RequestError:
                raise
            except Exception as e:
                logger.error(f"OpenAI streaming failed: {e}", exc_info=True)
                raise
            finally:
                if reasoning_accum:
                    result.thinking_content = "".join(reasoning_accum)
                merged_tools = _finalize_stream_tool_calls(tool_call_buffers)
                if merged_tools:
                    result.tool_calls = merged_tools

        result.stream = stream_generator()
        result.is_streaming = True

        # Store messages for token estimation (will be used by stream wrapper in base.py)
        result._messages_for_estimation = messages

        return result

    # ============================================================================
    # Helper Methods
    # ============================================================================

    def _estimate_cost(
        self, usage: Dict[str, Any], model_name: Optional[str] = None
    ) -> None:
        """Estimate cost based on token usage.

        Args:
            usage: Usage dict with prompt_tokens and completion_tokens (may include additional fields)
        """
        # Get pricing for current model
        pricing = self._model_pricing.get(model_name or self.model)
        if not pricing:
            # Use default pricing if model not found
            pricing = {"input": 1.0, "output": 2.0}

        # Calculate cost (pricing is per 1M tokens)
        # Extract integer values, ignoring any nested dict fields like prompt_tokens_details
        prompt_tokens = usage.get("prompt_tokens", 0) or 0
        completion_tokens = usage.get("completion_tokens", 0) or 0

        prompt_cost = (prompt_tokens / 1_000_000) * pricing["input"]
        completion_cost = (completion_tokens / 1_000_000) * pricing["output"]

        total_cost = prompt_cost + completion_cost
        self.total_cost += total_cost

        logger.debug(
            f"Estimated cost: ${total_cost:.6f} "
            f"(prompt: ${prompt_cost:.6f}, completion: ${completion_cost:.6f})"
        )

    async def track_usage(
        self, usage: Dict[str, int], duration: Optional[float] = None
    ) -> None:
        """Track usage and estimate cost.

        Overrides base implementation to add cost estimation.

        Args:
            usage: Usage dict with token counts
            duration: Query duration in seconds (optional)
        """
        await super().track_usage(usage, duration)
        self._estimate_cost(usage)
