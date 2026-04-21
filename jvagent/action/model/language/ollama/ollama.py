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

from jvagent.action.model.language.base import LanguageModelAction, ModelActionResult

logger = logging.getLogger(__name__)


class OllamaLanguageModelAction(LanguageModelAction):
    """Ollama language model integration action.

    Implements the LanguageModelAction interface for Ollama's native /api/chat
    endpoint.
    """

    api_endpoint: str = attribute(
        default="http://localhost:11434",
        description="Ollama API endpoint URL",
    )
    api_key: str = attribute(
        default="",
        description="Optional Ollama API key for hosted/cloud deployments",
    )
    model: str = attribute(default="llama3.1", description="Ollama model identifier")
    provider: str = attribute(default="ollama", description="Provider name")

    async def on_register(self) -> None:
        """Called when action is registered during installation."""
        await super().on_register()
        logger.info(f"Ollama action registered: {self.label} (model: {self.model})")

    def _build_headers(self) -> Dict[str, str]:
        """Build request headers.

        Local Ollama generally does not require auth.
        Hosted/cloud Ollama deployments may require bearer auth.
        """
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
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
        """Convert framework message format to Ollama-native message format."""
        ollama_messages: List[Dict[str, Any]] = []
        for message in messages:
            role = message.get("role", "user")
            content, images = self._extract_images(message.get("content", ""))
            normalized: Dict[str, Any] = {"role": role, "content": content}
            if images:
                normalized["images"] = images
            ollama_messages.append(normalized)
        return ollama_messages

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
                f"{self.api_endpoint.rstrip('/')}/api/chat",
                json=payload,
                headers=self._build_headers(),
            )
            response.raise_for_status()
            data = response.json()

            message = data.get("message", {})
            content = message.get("content", "")
            usage = self._extract_usage(data)

            return ModelActionResult(
                response=content,
                usage=usage,
                model=model_override,
                provider="ollama",
                finish_reason=data.get("done_reason"),
                tool_calls=self._normalize_tool_calls(message.get("tool_calls", [])),
            )
        except httpx.HTTPStatusError:
            raise
        except httpx.TimeoutException as e:
            logger.error(f"Ollama API timeout: {e}", exc_info=True)
            raise
        except httpx.RequestError as e:
            logger.error(f"Ollama API request failed: {e}", exc_info=True)
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
        await self._initialize_http_client()
        payload = self._build_payload(messages, tools, stream=True, **kwargs)
        model_override = kwargs.get("model", self.model)

        result = ModelActionResult(
            usage={},
            model=model_override,
            provider="ollama",
            finish_reason=None,
            tool_calls=[],
        )

        async def stream_generator() -> AsyncGenerator[str, None]:
            try:
                async with self._http_client.stream(  # type: ignore[union-attr]
                    "POST",
                    f"{self.api_endpoint.rstrip('/')}/api/chat",
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

                        if chunk.get("done"):
                            result.finish_reason = chunk.get("done_reason")
                            result.tool_calls = self._normalize_tool_calls(
                                msg.get("tool_calls", [])
                            )
                            result.metrics.update(self._extract_usage(chunk))
            except httpx.HTTPStatusError:
                raise
            except httpx.TimeoutException as e:
                logger.error(f"Ollama streaming timeout: {e}", exc_info=True)
                raise
            except httpx.RequestError as e:
                logger.error(f"Ollama streaming request failed: {e}", exc_info=True)
                raise
            except Exception as e:
                logger.error(f"Ollama streaming failed: {e}", exc_info=True)
                raise

        result.stream = stream_generator()
        result.is_streaming = True
        result._messages_for_estimation = messages
        return result
