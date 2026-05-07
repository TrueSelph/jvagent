"""Ollama model action implementation.

Provides integration with Ollama's native chat API with support for both
synchronous and streaming responses.

Supports both:
- Local Ollama (no API key required)
- Ollama Cloud or other hosted Ollama-native endpoints (API key optional/configurable)
"""

import json
import logging
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx
from jvspatial.core.annotations import attribute

from jvagent.action.model.language.base import (
    LanguageModelAction,
    ModelActionResult,
    ReasoningModelConfig,
)
from jvagent.action.model.ollama_endpoint import ollama_host_root

logger = logging.getLogger(__name__)


def _increment_ollama_thinking_stream(
    previous_snapshot: str, raw: Any
) -> tuple[str, str]:
    """Derive delta text from one Ollama streaming ``thinking`` fragment.

    The API commonly sends the **entire reasoning generated so far** on each NDJSON
    chunk. Treat any fragment that extends ``previous_snapshot`` as cumulative and
    emit only the suffix; otherwise treat the fragment as a standalone delta for
    append-only protocols.
    """
    if raw is None or raw == "":
        return previous_snapshot, ""
    s = str(raw)
    if not s:
        return previous_snapshot, ""
    if previous_snapshot and s.startswith(previous_snapshot):
        delta = s[len(previous_snapshot) :]
        return s, delta
    delta = s
    return previous_snapshot + s, delta


class OllamaLanguageModelAction(LanguageModelAction):
    """Ollama language model integration action.

    Implements the LanguageModelAction interface for Ollama's native /api/chat
    endpoint.
    """

    api_endpoint: str = attribute(
        default="http://localhost:11434",
        description=(
            "Ollama host root (e.g. http://localhost:11434 or https://ollama.com); "
            "may also be set to the docs-style API base ending in /api"
        ),
    )
    model: str = attribute(default="llama3.1", description="Ollama model identifier")
    provider: str = attribute(default="ollama", description="Provider name")
    reasoning_enabled: Optional[bool] = attribute(
        default=None,
        description="Default loop reasoning flag mapped to Ollama think=true.",
    )

    async def on_register(self) -> None:
        """Called when action is registered during installation."""
        await super().on_register()
        logger.info(f"Ollama action registered: {self.label} (model: {self.model})")

    def translate_reasoning_config(self, cfg: ReasoningModelConfig) -> Dict[str, Any]:
        if cfg.profile == "final":
            return {}
        enabled = cfg.reasoning_enabled
        if enabled is None:
            enabled = self.reasoning_enabled
        translated: Dict[str, Any] = {}
        if enabled is True:
            translated["think"] = True
        if cfg.reasoning_extra:
            translated.update(dict(cfg.reasoning_extra))
        return translated

    def _build_headers(self) -> Dict[str, str]:
        """Build request headers.

        Local Ollama generally does not require auth. Hosted/cloud Ollama
        deployments may require bearer auth via ``OLLAMA_API_KEY``.
        """
        headers = {"Content-Type": "application/json"}
        api_key = self.api_key_from_context("OLLAMA_API_KEY")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _extract_images(self, content: Any) -> tuple[str, List[str]]:
        """Extract text + base64 images from structured content."""
        if isinstance(content, str):
            return content, []
        if not isinstance(content, list):
            return str(content), []

        text_parts: List[str] = []
        images: List[str] = []

        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                text_parts.append(part.get("text", ""))
                continue
            if part.get("type") != "image_url":
                continue

            image_url = part.get("image_url", {})
            url = image_url.get("url") if isinstance(image_url, dict) else None
            if not isinstance(url, str):
                continue

            if url.startswith("data:image") and ";base64," in url:
                images.append(url.split(";base64,", 1)[1])
            else:
                logger.warning(
                    "Ollama currently supports base64/data URI images in this action; "
                    f"ignoring unsupported image URL format for action {self.label}"
                )

        return "\n".join([p for p in text_parts if p]), images

    def _to_ollama_messages(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Convert framework message format to Ollama-native message format.

        Critical: preserves ``tool_calls`` on assistant turns and ``tool_call_id``
        on tool-result turns so the model can see its own tool-use history. Without
        this, every iteration of an agentic loop is blind to prior tool calls and
        the model re-issues the same call on every turn (classic "stuck" pattern).
        """
        ollama_messages: List[Dict[str, Any]] = []
        for message in messages:
            role = message.get("role", "user")
            raw_content = message.get("content", "")
            # Tool/assistant turns may carry None content alongside tool_calls.
            if raw_content is None:
                content, images = "", []
            else:
                content, images = self._extract_images(raw_content)
            normalized: Dict[str, Any] = {"role": role, "content": content}
            if images:
                normalized["images"] = images

            # Preserve tool-call structure across turns so the model sees its
            # own tool-use history end-to-end.
            if role == "assistant":
                tool_calls = message.get("tool_calls")
                if tool_calls:
                    normalized["tool_calls"] = self._tool_calls_for_ollama(tool_calls)
            elif role == "tool":
                # Ollama's native /api/chat accepts ``tool_call_id`` and/or
                # ``name`` on tool messages — pass both when available so the
                # model can correlate result → call.
                tool_call_id = message.get("tool_call_id")
                if tool_call_id:
                    normalized["tool_call_id"] = tool_call_id
                tool_name = message.get("name") or message.get("tool_name")
                if tool_name:
                    normalized["name"] = tool_name

            ollama_messages.append(normalized)
        return ollama_messages

    @staticmethod
    def _tool_calls_for_ollama(
        tool_calls: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Reshape framework tool-call dicts to Ollama-native ``tool_calls`` format.

        Ollama expects ``arguments`` as a parsed object, not a JSON string.
        """
        out: List[Dict[str, Any]] = []
        for tc in tool_calls or []:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            name = fn.get("name") or tc.get("name") or ""
            args = fn.get("arguments")
            # Coerce JSON-string arguments back to a dict for the wire payload.
            if isinstance(args, str):
                try:
                    args = json.loads(args) if args.strip() else {}
                except (json.JSONDecodeError, ValueError):
                    args = {}
            elif args is None:
                args = {}
            entry: Dict[str, Any] = {
                "function": {
                    "name": name,
                    "arguments": args if isinstance(args, dict) else {},
                }
            }
            tid = tc.get("id")
            if tid:
                entry["id"] = tid
            out.append(entry)
        return out

    def _extract_usage(self, data: Dict[str, Any]) -> Dict[str, int]:
        """Map Ollama usage fields to the shared usage shape."""
        prompt_tokens = int(data.get("prompt_eval_count", 0) or 0)
        completion_tokens = int(data.get("eval_count", 0) or 0)
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }

    def _normalize_tool_calls(self, raw_tool_calls: Any) -> List[Dict[str, Any]]:
        """Normalize Ollama-native tool calls to OpenAI function-call shape."""
        if not isinstance(raw_tool_calls, list):
            return []

        normalized: List[Dict[str, Any]] = []
        for item in raw_tool_calls:
            if not isinstance(item, dict):
                continue

            function = item.get("function")
            if not isinstance(function, dict):
                continue

            tool_name = function.get("name")
            if not isinstance(tool_name, str) or not tool_name:
                continue

            arguments = function.get("arguments", {})
            if isinstance(arguments, dict):
                arguments_text = json.dumps(arguments)
            elif isinstance(arguments, str):
                arguments_text = arguments
            else:
                arguments_text = "{}"

            tool_id = item.get("id")
            if not isinstance(tool_id, str) or not tool_id:
                tool_id = f"call_{uuid.uuid4().hex[:12]}"

            normalized.append(
                {
                    "id": tool_id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": arguments_text,
                    },
                }
            )

        return normalized

    def _merge_stream_tool_calls(
        self,
        accumulated: List[Dict[str, Any]],
        chunk_normalized: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Merge normalized tool calls from a stream chunk into accumulated list.

        Later chunks may complete or replace arguments for the same tool id.
        """
        if not chunk_normalized:
            return accumulated
        by_id: Dict[str, int] = {}
        for i, tc in enumerate(accumulated):
            tid = tc.get("id")
            if isinstance(tid, str) and tid:
                by_id[tid] = i
        merged = list(accumulated)
        for tc in chunk_normalized:
            tid = tc.get("id")
            if isinstance(tid, str) and tid and tid in by_id:
                idx = by_id[tid]
                old_fn = merged[idx].get("function", {})
                new_fn = tc.get("function", {})
                old_args = old_fn.get("arguments", "{}")
                new_args = new_fn.get("arguments", "{}")
                if isinstance(old_args, str) and isinstance(new_args, str):
                    if len(new_args) >= len(old_args):
                        merged[idx]["function"]["arguments"] = new_args
                else:
                    merged[idx] = tc
            else:
                merged.append(tc)
                tid2 = tc.get("id")
                if isinstance(tid2, str) and tid2:
                    by_id[tid2] = len(merged) - 1
        return merged

    def _build_payload(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        model_override = kwargs.get("model", self.model)
        options = {
            "temperature": kwargs.get("temperature", self.temperature),
            "top_p": kwargs.get("top_p", self.top_p),
            "num_predict": kwargs.get("max_tokens", self.max_tokens),
        }
        payload: Dict[str, Any] = {
            "model": model_override,
            "messages": self._to_ollama_messages(messages),
            "stream": stream,
            "options": options,
        }
        if tools:
            payload["tools"] = tools
        reasoning = kwargs.get("reasoning")
        if isinstance(reasoning, dict) and reasoning.get("think") is True:
            payload["think"] = True
        elif kwargs.get("think") is True:
            payload["think"] = True
        return payload

    async def _query(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> ModelActionResult:
        """Execute a synchronous query to Ollama."""
        await self._initialize_http_client()
        payload = self._build_payload(messages, tools, stream=False, **kwargs)
        model_override = kwargs.get("model", self.model)

        try:
            response = await self._http_client.post(  # type: ignore[union-attr]
                f"{ollama_host_root(self.api_endpoint)}/api/chat",
                json=payload,
                headers=self._build_headers(),
            )
            response.raise_for_status()
            data = response.json()

            message = data.get("message", {})
            content = message.get("content", "")
            usage = self._extract_usage(data)
            thinking_raw = message.get("thinking") or ""
            thinking_content = str(thinking_raw) if thinking_raw else None

            return ModelActionResult(
                response=content,
                usage=usage,
                model=model_override,
                provider="ollama",
                finish_reason=data.get("done_reason"),
                tool_calls=self._normalize_tool_calls(message.get("tool_calls", [])),
                thinking_content=thinking_content,
            )
        except httpx.HTTPStatusError:
            raise
        except httpx.TimeoutException:
            raise
        except httpx.RequestError:
            raise
        except Exception as e:
            logger.error(f"Ollama query failed: {e}", exc_info=True)
            raise

    async def _query_stream(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> ModelActionResult:
        """Execute a streaming query to Ollama."""
        thinking_queue = kwargs.get("_jv_thinking_queue")
        await self._initialize_http_client()
        payload = self._build_payload(messages, tools, stream=True, **kwargs)
        model_override = kwargs.get("model", self.model)

        result = ModelActionResult(
            usage={},
            model=model_override,
            provider="ollama",
            finish_reason=None,
            tool_calls=[],
            thinking_queue=thinking_queue,
        )
        thinking_chunks: List[str] = []
        accumulated_tool_calls: List[Dict[str, Any]] = []
        thinking_snapshot: str = ""

        def _emit_thinking_fragment(raw: Any) -> None:
            nonlocal thinking_snapshot
            thinking_snapshot, delta = _increment_ollama_thinking_stream(
                thinking_snapshot, raw
            )
            if not delta:
                return
            thinking_chunks.append(delta)
            result.push_thinking_delta(delta)

        async def stream_generator() -> AsyncGenerator[str, None]:
            nonlocal accumulated_tool_calls
            try:
                async with self._http_client.stream(  # type: ignore[union-attr]
                    "POST",
                    f"{ollama_host_root(self.api_endpoint)}/api/chat",
                    json=payload,
                    headers=self._build_headers(),
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError:
                            logger.warning(
                                f"Failed to parse Ollama stream chunk: {line}"
                            )
                            continue

                        msg = chunk.get("message", {})
                        text_chunk = msg.get("content", "")
                        if text_chunk:
                            yield text_chunk

                        think_chunk = msg.get("thinking")
                        if think_chunk:
                            _emit_thinking_fragment(think_chunk)

                        raw_tc = msg.get("tool_calls")
                        if raw_tc:
                            batch = self._normalize_tool_calls(raw_tc)
                            accumulated_tool_calls = self._merge_stream_tool_calls(
                                accumulated_tool_calls, batch
                            )

                        if chunk.get("done"):
                            result.finish_reason = chunk.get("done_reason")
                            # tool_calls for this line were already merged via raw_tc above;
                            # do not merge again (would duplicate when done chunk includes tools).
                            result.tool_calls = accumulated_tool_calls
                            result.metrics.update(self._extract_usage(chunk))
            except httpx.HTTPStatusError:
                raise
            except httpx.TimeoutException:
                raise
            except httpx.RequestError:
                raise
            except Exception as e:
                logger.error(f"Ollama streaming failed: {e}", exc_info=True)
                raise
            finally:
                if thinking_chunks:
                    result.thinking_content = "".join(thinking_chunks)
                if accumulated_tool_calls and not result.tool_calls:
                    result.tool_calls = accumulated_tool_calls

        result.stream = stream_generator()
        result.is_streaming = True
        result._messages_for_estimation = messages
        return result
