"""Base classes for model actions.

This module provides the core abstractions for model integrations:
- BaseModelAction: Generic base class with common attributes and operations
"""

import asyncio
import logging
import time
from abc import ABC
from typing import Any, Dict, List, Optional

import httpx
from jvspatial.core.annotations import attribute

from jvagent.action.base import Action

logger = logging.getLogger(__name__)


class BaseModelAction(Action, ABC):
    """Base class for all model actions with common attributes and operations.

    This class provides the foundation for all model action types (Language Model, Embedding, etc.)
    with shared configuration, metrics tracking, and lifecycle management.

    Common Attributes:
        api_endpoint: Base API endpoint URL
        model: Model identifier/name
        timeout: Request timeout in seconds

    Common Metrics:
        total_requests: Total number of requests made
        total_tokens: Cumulative token usage
        total_cost: Estimated cost in USD
        total_duration: Cumulative query duration in seconds
    """

    # Common configuration attributes
    api_endpoint: str = attribute(default="", description="API endpoint URL")
    model: str = attribute(default="", description="Model identifier")
    timeout: int = attribute(default=30, description="Request timeout in seconds", ge=1)

    # Common metrics attributes
    total_requests: int = attribute(default=0, description="Total number of requests")
    total_tokens: int = attribute(default=0, description="Cumulative token usage")
    total_cost: float = attribute(default=0.0, description="Estimated cost in USD")
    total_duration: float = attribute(
        default=0.0, description="Cumulative query duration in seconds"
    )

    # HTTP client (not persisted)
    _http_client: Optional[httpx.AsyncClient] = attribute(private=True, default=None)

    async def track_usage(
        self,
        usage: Dict[str, int],
        duration: Optional[float] = None,
    ) -> None:
        """Track token usage and update metrics.

        Automatically emits observability events using interaction from context.
        Awaits emission so events are written to the interaction immediately.

        Args:
            usage: Usage dict with token counts
            duration: Query duration in seconds (optional)
        """
        total = usage.get("total_tokens", 0)
        self.total_requests += 1
        self.total_tokens += total

        if duration is not None:
            self.total_duration += duration

        # Cost estimation can be overridden by providers
        # Base implementation doesn't estimate cost
        duration_str = f"{duration:.3f}s" if duration is not None else "n/a"
        logger.debug(
            f"Tracked usage: {total} tokens, {duration_str} (total: {self.total_tokens} tokens, "
            f"{self.total_duration:.3f}s, requests: {self.total_requests})"
        )

        # Emit observability event directly to interaction
        try:
            from jvagent.action.model.context import get_interaction

            interaction = get_interaction()
            if interaction:
                await self._emit_observability(interaction, usage, duration)
        except Exception as e:
            logger.debug(f"Failed to emit observability: {e}")

    async def _emit_observability(
        self,
        interaction: Any,
        usage: Dict[str, int],
        duration: Optional[float],
    ) -> None:
        """Emit observability event directly to the interaction.

        Args:
            interaction: Interaction object to write to
            usage: Usage dict with token counts
            duration: Query duration in seconds
        """
        try:
            # Determine event type based on class name
            class_name = self.__class__.__name__
            if "Embedding" in class_name:
                event_type = "embedding_call"
            else:
                event_type = "model_call"

            # Get result for provider and response data
            result = None
            if hasattr(self, "_last_result"):
                result = getattr(self, "_last_result", None)

            # Get provider from result if available, otherwise from self
            # Implementing classes must set provider attribute explicitly
            provider = "unknown"
            if result and hasattr(result, "provider") and result.provider:
                provider = result.provider
            elif hasattr(self, "provider") and self.provider:
                provider = self.provider

            # Check if usage is estimated (for streaming results)
            usage_estimated = False
            if result and hasattr(result, "_usage_estimated"):
                usage_estimated = getattr(result, "_usage_estimated", False)

            # Use updated metrics from result if available (for streaming that completed)
            if result and hasattr(result, "metrics"):
                result_metrics = result.metrics
                # Check if result has updated usage (from token estimation)
                if any(
                    result_metrics.get(key, 0) > 0
                    for key in ["prompt_tokens", "completion_tokens", "total_tokens"]
                ):
                    # Use the updated metrics from result
                    usage = {
                        "prompt_tokens": result_metrics.get("prompt_tokens", 0),
                        "completion_tokens": result_metrics.get("completion_tokens", 0),
                        "total_tokens": result_metrics.get("total_tokens", 0),
                    }
                    usage_estimated = getattr(result, "_usage_estimated", False)

            # Get model from result if available (actual model used), otherwise fall back to self.model
            # This ensures we report the actual model used (e.g., from PersonaAction override)
            # rather than the LanguageModelAction's default model
            model = ""
            if result and hasattr(result, "model") and result.model:
                model = result.model
            elif hasattr(self, "model") and self.model:
                model = self.model

            # Get calling action name from result, fallback on context then model action
            action_name = None
            if (
                result
                and hasattr(result, "calling_action_name")
                and result.calling_action_name
            ):
                action_name = result.calling_action_name
            elif hasattr(self, "_calling_action_name") and self._calling_action_name:
                action_name = self._calling_action_name
            elif hasattr(self, "_action_name") and self._action_name:
                action_name = self._action_name
            else:
                from jvagent.action.model.context import get_calling_action_name

                action_name = get_calling_action_name() or self.get_class_name()

            # Get system prompt, user prompt, and history from result
            system_prompt = None
            user_prompt = None
            history = None
            if result:
                if hasattr(result, "system") and result.system:
                    system_prompt = result.system
                if hasattr(result, "prompt") and result.prompt:
                    user_prompt = result.prompt
                if hasattr(result, "history") and result.history:
                    history = result.history

            # Build comprehensive observability data
            data = {
                "provider": provider,
                "model": model,
                "usage": usage,
                "duration": duration,
                "estimated": usage_estimated,  # Flag to indicate estimated vs actual metrics
                "called_by": action_name,  # Always include called_by with action name
            }

            # Add system prompt (the actual prompt that was executed)
            if system_prompt:
                data["system_prompt"] = system_prompt

            # Add user prompt (the user's input)
            if user_prompt:
                data["user_prompt"] = user_prompt

            # Add history (conversation history) for observability
            if history:
                data["history"] = history

            # For language models, try to include response if available
            # This is a best-effort attempt - response may not be available at track_usage time
            if result:
                # Get response text (handle both sync and streaming results)
                # For streaming results, only get response if already cached (stream consumed)
                # to avoid interfering with the caller's stream consumption
                response_text = None
                is_streaming = getattr(result, "is_streaming", False)

                if hasattr(result, "response") and result.response:
                    # Response is already available (cached or sync)
                    response_text = result.response
                elif hasattr(result, "get_response") and not is_streaming:
                    # For non-streaming, safe to call get_response()
                    try:
                        response_coro = result.get_response()
                        if asyncio.iscoroutine(response_coro):
                            response_text = await response_coro
                        else:
                            response_text = response_coro
                    except Exception:
                        pass
                elif is_streaming:
                    # For streaming, only include response if already cached
                    # (stream has been consumed by caller)
                    if hasattr(result, "response") and result.response:
                        response_text = result.response

                if response_text:
                    data["response"] = response_text

                data["is_streaming"] = is_streaming

                if hasattr(result, "finish_reason") and result.finish_reason:
                    data["finish_reason"] = result.finish_reason
                if hasattr(result, "tool_calls") and result.tool_calls:
                    data["tool_calls"] = result.tool_calls

            # Build event and append directly to interaction
            event = {
                "event_type": event_type,
                "data": data,
                "timestamp": time.time(),
            }
            interaction.observability_metrics.append(event)

            # Save to mark dirty (with deferred saves enabled, this just sets _dirty = True)
            await interaction.save()

        except Exception as e:
            logger.debug(f"Failed to emit observability event: {e}")

    async def _initialize_http_client(self) -> None:
        """Initialize HTTP client with connection pooling.

        This method can be called multiple times safely - it will only initialize
        the client if it doesn't already exist. Called automatically during
        on_register() and when HTTP client is needed for queries.
        """
        if self._http_client is not None:
            return

        # Initialize HTTP client with connection pooling
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )

        logger.debug(f"HTTP client initialized (endpoint: {self.api_endpoint})")

    async def on_register(self) -> None:
        """Called when action is registered.

        Providers should override this to validate configuration.
        HTTP client initialization is handled automatically.
        """
        logger.info(f"Model action registered: {self.label} (model: {self.model})")

        # Initialize HTTP client automatically
        await self._initialize_http_client()

    async def on_disable(self) -> None:
        """Called when action is disabled.

        Providers should override this to clean up resources.
        HTTP client cleanup is handled automatically.
        """
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
            logger.debug("HTTP client closed")

        logger.info(f"Model action disabled: {self.label}")
