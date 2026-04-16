"""Anthropic model action implementation.

Provides integration with Anthropic's native Messages API with support for
both synchronous and streaming responses, tool calling, and multimodal input.
"""

import base64
import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

import httpx
from jvspatial.core.annotations import attribute
from jvspatial.env import env

from jvagent.action.model.language.base import LanguageModelAction, ModelActionResult

logger = logging.getLogger(__name__)


class AnthropicLanguageModelAction(LanguageModelAction):
    """Anthropic language model integration action."""

    api_endpoint: str = attribute(
        default="https://api.anthropic.com/v1", description="Anthropic API endpoint URL"
    )
    model: str = attribute(default="claude-3-5-sonnet-latest", description="Model id")
    provider: str = attribute(default="anthropic", description="Provider name")
    anthropic_version: str = attribute(
        default="2023-06-01", description="Anthropic API version header value"
    )

    async def on_register(self) -> None:
        """Called when action is registered during installation."""
        await super().on_register()

        if not env("ANTHROPIC_API_KEY"):
            logger.warning(f"Anthropic action {self.label} has no API key configured")

    def _extract_system_and_messages(
        self, messages: List[Dict[str, Any]]
    ) -> Tuple[Optional[str], List[Dict[str, Any]]]:
        """Extract system prompt and normalize non-system messages."""
        system_parts: List[str] = []
        normalized: List[Dict[str, Any]] = []

        for message in messages:
            role = message.get("role", "user")
            if role == "system":
                content = message.get("content", "")
                if isinstance(content, str):
                    if content:
                        system_parts.append(content)
                elif isinstance(content, list):
                    text_parts = [
                        part.get("text", "")
                        for part in content
                        if isinstance(part, dict) and part.get("type") == "text"
                    ]
                    if text_parts:
                        system_parts.append("\n".join([t for t in text_parts if t]))
                else:
                    system_parts.append(str(content))
                continue

            normalized.append(message)

        system_text = "\n\n".join([part for part in system_parts if part]) or None
        return system_text, normalized

    def _extract_image_source(self, url: str) -> Optional[Dict[str, str]]:
        """Convert image url/data-uri to Anthropic image block source."""
        if url.startswith("data:image") and ";base64," in url:
            header, data = url.split(",", 1)
            media_type = header.split(";", 1)[0].replace("data:", "", 1)
            return {"type": "base64", "media_type": media_type, "data": data}

        if url.startswith("http://") or url.startswith("https://"):
            return {"type": "url", "url": url}

        try:
            # If input is already raw base64 data, Anthropic requires media_type.
            base64.b64decode(url, validate=True)
            return {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": url,
            }
        except Exception:
            logger.warning(
                "Ignoring unsupported image_url format in Anthropic action %s",
                self.label,
            )
            return None

    def _normalize_content(self, content: Any) -> Any:
        """Convert framework content parts to Anthropic content blocks."""
        if isinstance(content, str):
            return content

        if not isinstance(content, list):
            return str(content)

        blocks: List[Dict[str, Any]] = []
        text_chunks: List[str] = []

        for part in content:
            if not isinstance(part, dict):
                text_chunks.append(str(part))
                continue

            part_type = part.get("type")
            if part_type == "text":
                text = part.get("text", "")
                if text:
                    blocks.append({"type": "text", "text": text})
                continue

            if part_type == "image_url":
                image_url = part.get("image_url", {})
                url = image_url.get("url") if isinstance(image_url, dict) else None
                if not isinstance(url, str):
                    continue
                source = self._extract_image_source(url)
                if source:
                    # Anthropic image block supports URL or base64 source.
                    if source["type"] == "url":
                        blocks.append(
                            {
                                "type": "image",
                                "source": {"type": "url", "url": source["url"]},
                            }
                        )
                    else:
                        blocks.append(
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": source.get(
                                        "media_type", "image/jpeg"
                                    ),
                                    "data": source.get("data", ""),
                                },
                            }
                        )
                continue

            text_chunks.append(str(part))

        if blocks:
            return blocks

        if text_chunks:
            return "\n".join(text_chunks)

        return ""

    def _map_tools(
        self, tools: Optional[List[Dict[str, Any]]]
    ) -> Optional[List[Dict[str, Any]]]:
        """Map OpenAI-style tool definitions to Anthropic tool schema."""
        if not tools:
            return None

        mapped: List[Dict[str, Any]] = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue

            if tool.get("type") != "function":
                continue

            fn = tool.get("function", {})
            if not isinstance(fn, dict):
                continue

            name = fn.get("name")
            if not name:
                continue

            mapped.append(
                {
                    "name": name,
                    "description": fn.get("description", ""),
                    "input_schema": fn.get(
                        "parameters", {"type": "object", "properties": {}}
                    ),
                }
            )

        return mapped or None

    def _build_payload(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Build Anthropic Messages API payload."""
        system_text, normalized_messages = self._extract_system_and_messages(messages)
        anthropic_messages = [
            {
                "role": message.get("role", "user"),
                "content": self._normalize_content(message.get("content", "")),
            }
            for message in normalized_messages
        ]

        model_override = kwargs.get("model", self.model)
        payload: Dict[str, Any] = {
            "model": model_override,
            "messages": anthropic_messages,
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "top_p": kwargs.get("top_p", self.top_p),
            "stream": stream,
        }

        # Extended thinking: when enabled, temperature must be omitted
        thinking_config = kwargs.get("thinking")
        if thinking_config and isinstance(thinking_config, dict):
            payload["thinking"] = thinking_config
            # Anthropic requires max_tokens >= budget_tokens + 1
            budget = thinking_config.get("budget_tokens", 0)
            if payload["max_tokens"] < budget + 1:
                payload["max_tokens"] = budget + 1
        else:
            payload["temperature"] = kwargs.get("temperature", self.temperature)

        if system_text:
            payload["system"] = system_text

        mapped_tools = self._map_tools(tools)
        if mapped_tools:
            payload["tools"] = mapped_tools

        return payload

    def _extract_usage(self, data: Dict[str, Any]) -> Dict[str, int]:
        usage = data.get("usage", {})
        prompt_tokens = int(usage.get("input_tokens", 0) or 0)
        completion_tokens = int(usage.get("output_tokens", 0) or 0)
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }

    def _extract_result_fields(
        self, data: Dict[str, Any]
    ) -> Tuple[str, List[Dict[str, Any]], Optional[str]]:
        content_items = data.get("content", [])
        text_parts: List[str] = []
        thinking_parts: List[str] = []
        tool_calls: List[Dict[str, Any]] = []

        for block in content_items:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "thinking":
                thinking_text = block.get("thinking", "")
                if thinking_text:
                    thinking_parts.append(thinking_text)
            elif block_type == "text":
                text = block.get("text", "")
                if text:
                    text_parts.append(text)
            elif block_type == "tool_use":
                tool_calls.append(
                    {
                        "id": block.get("id"),
                        "type": "function",
                        "function": {
                            "name": block.get("name"),
                            "arguments": json.dumps(block.get("input", {})),
                        },
                    }
                )

        finish_reason = data.get("stop_reason")
        return "".join(text_parts), tool_calls, finish_reason

    def _headers(self) -> Dict[str, str]:
        api_key = env("ANTHROPIC_API_KEY") or ""
        return {
            "x-api-key": api_key,
            "anthropic-version": self.anthropic_version,
            "content-type": "application/json",
        }

    async def _query(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> ModelActionResult:
        """Execute a synchronous query to Anthropic."""
        await self._initialize_http_client()
        payload = self._build_payload(messages, tools=tools, stream=False, **kwargs)
        model_override = kwargs.get("model", self.model)

        try:
            response = await self._http_client.post(  # type: ignore[union-attr]
                f"{self.api_endpoint}/messages",
                json=payload,
                headers=self._headers(),
            )
            response.raise_for_status()
            data = response.json()

            response_text, tool_calls, finish_reason = self._extract_result_fields(data)
            usage = self._extract_usage(data)

            # Extract thinking content if present
            thinking_content = None
            thinking_tokens = None
            thinking_config = kwargs.get("thinking")
            if thinking_config:
                content_items = data.get("content", [])
                thinking_parts = [
                    block.get("thinking", "")
                    for block in content_items
                    if isinstance(block, dict) and block.get("type") == "thinking"
                ]
                thinking_content = "".join(thinking_parts) or None
                thinking_tokens = usage.get("output_tokens", 0)

            return ModelActionResult(
                response=response_text,
                usage=usage,
                model=model_override,
                provider="anthropic",
                finish_reason=finish_reason,
                tool_calls=tool_calls,
                thinking_content=thinking_content,
                thinking_tokens=thinking_tokens,
            )
        except httpx.HTTPStatusError:
            raise
        except httpx.TimeoutException as e:
            logger.error(f"Anthropic API timeout: {e}", exc_info=True)
            raise
        except httpx.RequestError as e:
            logger.error(f"Anthropic API request failed: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Anthropic query failed: {e}", exc_info=True)
            raise

    async def _query_stream(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> ModelActionResult:
        """Execute a streaming query to Anthropic."""
        await self._initialize_http_client()
        payload = self._build_payload(messages, tools=tools, stream=True, **kwargs)
        model_override = kwargs.get("model", self.model)

        result = ModelActionResult(
            usage={},
            model=model_override,
            provider="anthropic",
            finish_reason=None,
            tool_calls=[],
            thinking_content=None,
            thinking_tokens=None,
        )

        async def stream_generator() -> AsyncGenerator[str, None]:
            text_chunks: List[str] = []
            thinking_chunks: List[str] = []

            try:
                async with self._http_client.stream(  # type: ignore[union-attr]
                    "POST",
                    f"{self.api_endpoint}/messages",
                    json=payload,
                    headers=self._headers(),
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line or line.startswith(":"):
                            continue

                        if not line.startswith("data: "):
                            continue

                        data_str = line[6:]
                        if not data_str:
                            continue

                        try:
                            event = json.loads(data_str)
                        except json.JSONDecodeError:
                            logger.warning(
                                "Failed to parse Anthropic streaming event: %s",
                                data_str,
                            )
                            continue

                        event_type = event.get("type")
                        if event_type == "content_block_delta":
                            delta = event.get("delta", {})
                            delta_type = delta.get("type")
                            if delta_type == "text_delta":
                                chunk = delta.get("text", "")
                                if chunk:
                                    text_chunks.append(chunk)
                                    yield chunk
                            elif delta_type == "thinking_delta":
                                chunk = delta.get("thinking", "")
                                if chunk:
                                    thinking_chunks.append(chunk)
                        elif event_type == "message_delta":
                            delta = event.get("delta", {})
                            stop_reason = delta.get("stop_reason")
                            if stop_reason:
                                result.finish_reason = stop_reason
                            usage_delta = delta.get("usage", {})
                            if usage_delta:
                                output_tokens = usage_delta.get("output_tokens", 0)
                                if output_tokens:
                                    result.thinking_tokens = output_tokens
                        elif event_type == "message_stop":
                            message = event.get("message", {})
                            if isinstance(message, dict):
                                usage = self._extract_usage(message)
                                if usage["total_tokens"] > 0:
                                    result.metrics.update(usage)
                                _, tool_calls, finish_reason = (
                                    self._extract_result_fields(message)
                                )
                                if tool_calls:
                                    result.tool_calls = tool_calls
                                if finish_reason:
                                    result.finish_reason = finish_reason
                            # Store accumulated thinking content
                            if thinking_chunks:
                                result.thinking_content = "".join(thinking_chunks)
                        elif event_type == "error":
                            error_data = event.get("error", {})
                            message_text = error_data.get(
                                "message", "Anthropic streaming error"
                            )
                            raise RuntimeError(message_text)
            except httpx.HTTPStatusError:
                raise
            except httpx.TimeoutException as e:
                logger.error(f"Anthropic streaming timeout: {e}", exc_info=True)
                raise
            except httpx.RequestError as e:
                logger.error(f"Anthropic streaming request failed: {e}", exc_info=True)
                raise
            except Exception as e:
                logger.error(f"Anthropic streaming failed: {e}", exc_info=True)
                raise

        result.stream = stream_generator()
        result.is_streaming = True
        result._messages_for_estimation = messages
        return result
