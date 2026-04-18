"""OpenAI model action implementation.

Provides integration with OpenAI's Chat Completions API with support for
both synchronous and streaming responses. Supports multimodal queries
(text + images) for visual understanding.
"""

import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx
from jvspatial.core.annotations import attribute

from jvagent.action.model.language.base import LanguageModelAction, ModelActionResult

logger = logging.getLogger(__name__)


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

    # Pricing per 1M tokens (approximate, for cost estimation)
    _model_pricing: Dict[str, Dict[str, float]] = attribute(
        private=True,
        default_factory=lambda: {
            "gpt-4o": {"input": 2.50, "output": 10.00},
            "gpt-4o-mini": {"input": 0.150, "output": 0.600},
            "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
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

    def _http_bearer_token(self) -> str:
        """Bearer token for Authorization header (subclasses may change env fallbacks)."""
        return self.api_key_from_context("OPENAI_API_KEY")

    # ============================================================================
    # Query Implementation
    # ============================================================================

    async def _query(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> ModelActionResult:
        """Execute a synchronous query to OpenAI."""
        await self._initialize_http_client()
        extra_headers = kwargs.pop("_extra_headers", None)

        # Build request payload
        # Use model from kwargs if provided, otherwise use instance default
        model_override = kwargs.get("model", self.model)
        payload = {
            "model": model_override,
            "messages": messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "top_p": kwargs.get("top_p", self.top_p),
        }

        # Add tools if provided
        if tools:
            payload["tools"] = tools

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
            response.raise_for_status()
            data = response.json()

            # Extract response
            choice = data["choices"][0]
            message = choice["message"]
            content = message.get("content", "")
            finish_reason = choice.get("finish_reason")

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
                provider="openai",
                finish_reason=finish_reason,
                tool_calls=tool_calls,
            )

        except httpx.HTTPStatusError as e:
            # Re-raise immediately - let the error handler log and format the response
            # This prevents duplicate logging and ensures consistent error formatting
            raise
        except httpx.TimeoutException as e:
            logger.error(f"OpenAI API timeout: {e}", exc_info=True)
            raise
        except httpx.RequestError as e:
            logger.error(f"OpenAI API request failed: {e}", exc_info=True)
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
        await self._initialize_http_client()
        extra_headers = kwargs.pop("_extra_headers", None)

        # Build request payload
        # Use model from kwargs if provided, otherwise use instance default
        model_override = kwargs.get("model", self.model)
        payload = {
            "model": model_override,
            "messages": messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "top_p": kwargs.get("top_p", self.top_p),
            "stream": True,
        }

        # Add tools if provided
        if tools:
            payload["tools"] = tools

        # Create streaming generator
        async def stream_generator() -> AsyncGenerator[str, None]:
            """Generate streaming chunks from OpenAI API."""
            finish_reason = None
            accumulated_chunks = []

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
                    response.raise_for_status()

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
                                choice = data["choices"][0]

                                # Extract content delta
                                delta = choice.get("delta", {})
                                content = delta.get("content", "")

                                if content:
                                    accumulated_chunks.append(content)
                                    yield content

                                # Track finish reason
                                if "finish_reason" in choice:
                                    finish_reason = choice["finish_reason"]

                                # OpenAI doesn't provide usage in stream by default
                                # Token estimation will be handled by the stream wrapper in base.py

                            except json.JSONDecodeError:
                                logger.warning(f"Failed to parse SSE data: {data_str}")
                                continue

            except httpx.HTTPStatusError as e:
                # Re-raise immediately - let the error handler log and format the response
                raise
            except httpx.TimeoutException as e:
                logger.error(f"OpenAI streaming timeout: {e}", exc_info=True)
                raise
            except httpx.RequestError as e:
                logger.error(f"OpenAI streaming request failed: {e}", exc_info=True)
                raise
            except Exception as e:
                logger.error(f"OpenAI streaming failed: {e}", exc_info=True)
                raise

        result = ModelActionResult(
            stream=stream_generator(),
            usage={},  # Usage not available in streaming mode by default
            model=model_override,  # Use the actual model used for this query
            provider="openai",
            finish_reason=None,
            tool_calls=[],
        )

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
