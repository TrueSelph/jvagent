"""OpenRouter model action implementation.

Provides integration with OpenRouter's API, which is OpenAI-compatible and
supports multiple language model providers through a single interface.
Supports multimodal queries (text + images) where the underlying provider supports it.
"""

import logging
from typing import Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.model.language.base import ReasoningModelConfig
from jvagent.action.model.language.openai.openai import OpenAILanguageModelAction

logger = logging.getLogger(__name__)


class OpenRouterLanguageModelAction(OpenAILanguageModelAction):
    """OpenRouter language model integration action.

    Implements the LanguageModelAction interface using OpenRouter's API, which is
    OpenAI-compatible. Supports multiple providers including OpenAI, Anthropic,
    Google, Meta, and others. Supports multimodal queries (text + images) where
    the underlying provider supports it.

    Configuration:
        api_key: OpenRouter API key (from environment or config)
        api_endpoint: OpenRouter API endpoint
        model: Model identifier (e.g., 'openai/gpt-4o', 'anthropic/claude-3.5-sonnet')
        temperature: Sampling temperature
        max_tokens: Maximum tokens to generate
        top_p: Nucleus sampling parameter
        http_referer: HTTP Referer header (optional, for OpenRouter)
        site_name: Site name for OpenRouter (optional)

    Examples:
        Programmatic usage:
        >>> action = await OpenRouterLanguageModelAction.get(action_id)
        >>> result = await action.query_sync("What is AI?", system="You are an expert")
        >>> response = await result.get_response()

        Using Anthropic models:
        >>> action.model = "anthropic/claude-3.5-sonnet"
        >>> result = await action.query_sync("Explain quantum physics")
    """

    # OpenRouter-specific configuration
    api_endpoint: str = attribute(
        default="https://openrouter.ai/api/v1",
        description="OpenRouter API endpoint URL",
    )
    model: str = attribute(
        default="openai/gpt-4o-mini",
        description="OpenRouter model identifier (provider/model format)",
    )
    provider: str = attribute(default="openrouter", description="Provider name")
    http_referer: str = attribute(
        default="", description="HTTP Referer header for OpenRouter (optional)"
    )
    site_name: str = attribute(
        default="jvagent", description="Site name for OpenRouter (optional)"
    )

    # OpenRouter pricing varies by model, so we don't provide defaults
    _model_pricing: Dict[str, Dict[str, float]] = attribute(
        private=True, default_factory=dict
    )

    # ============================================================================
    # Lifecycle Hooks
    # ============================================================================

    def _http_bearer_token(self) -> str:
        return self.api_key_from_context("OPENROUTER_API_KEY", "OPENAI_API_KEY")

    def _detect_reasoning_model(self, model_id: str, **kwargs: Any) -> bool:
        """OpenRouter uses nested ``reasoning: {effort: ...}``; do not reshape like native OpenAI."""
        return False

    def translate_reasoning_config(self, cfg: ReasoningModelConfig) -> Dict[str, Any]:
        if cfg.profile == "final":
            return {}
        effort = cfg.reasoning_effort
        extra = dict(cfg.reasoning_extra or {})
        enabled = cfg.reasoning_enabled
        should_emit = bool(effort) or bool(extra) or enabled is True
        if enabled is False:
            should_emit = False
        if not should_emit:
            return {}
        reasoning: Dict[str, Any] = {}
        if effort:
            reasoning["effort"] = str(effort)
        reasoning.update(extra)
        return {"reasoning": reasoning}

    async def on_register(self) -> None:
        """Initialize HTTP client and validate configuration."""
        # Call parent initialization (OpenAI)
        await super().on_register()

        logger.info(f"OpenRouter action registered: {self.label} (model: {self.model})")

    # ============================================================================
    # Query Implementation
    # ============================================================================

    async def _query(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> Any:
        """Execute a synchronous query to OpenRouter.

        OpenRouter uses the same API format as OpenAI, so we inherit the
        parent implementation but add OpenRouter-specific headers.

        Args:
            messages: List of message dicts
            tools: Optional tool definitions
            **kwargs: Additional parameters

        Returns:
            ModelActionResult with complete response
        """
        extra_headers: Dict[str, str] = {}
        if self.http_referer:
            extra_headers["HTTP-Referer"] = self.http_referer

        if self.site_name:
            extra_headers["X-Title"] = self.site_name

        # Call parent OpenAI implementation with per-request headers.
        result = await super()._query(
            messages,
            tools,
            _extra_headers=extra_headers or None,
            **kwargs,
        )
        result.provider = "openrouter"
        return result

    async def _query_stream(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> Any:
        """Execute a streaming query to OpenRouter.

        Args:
            messages: List of message dicts
            tools: Optional tool definitions
            **kwargs: Additional parameters

        Returns:
            ModelActionResult with streaming generator
        """
        extra_headers: Dict[str, str] = {}
        if self.http_referer:
            extra_headers["HTTP-Referer"] = self.http_referer

        if self.site_name:
            extra_headers["X-Title"] = self.site_name

        result = await super()._query_stream(
            messages,
            tools,
            _extra_headers=extra_headers or None,
            **kwargs,
        )

        result.provider = "openrouter"

        # Ensure messages are stored for token estimation (parent should have done this, but ensure it)
        if not hasattr(result, "_messages_for_estimation"):
            result._messages_for_estimation = messages
        if not hasattr(result, "_model_for_estimation"):
            result._model_for_estimation = kwargs.get("model", self.model)
        if not hasattr(result, "_provider_for_estimation"):
            result._provider_for_estimation = "openrouter"

        return result

    # ============================================================================
    # Helper Methods
    # ============================================================================

    def _estimate_cost(
        self,
        usage: Dict[str, Any],
        model_name: Optional[str] = None,
    ) -> None:
        """Estimate cost based on token usage.

        OpenRouter provides pricing info in the response, but for estimation
        purposes we use a generic rate if model-specific pricing isn't available.

        Args:
            usage: Usage dict with token counts
            model_name: Model id used for the request (per-call override)
        """
        mid = model_name or self.model
        # Check if we have pricing for this model
        pricing = self._model_pricing.get(mid)

        if pricing:
            # Use model-specific pricing
            super()._estimate_cost(usage, model_name=mid)
        else:
            # Use generic estimation for OpenRouter
            # Approximate: $1 per 1M input tokens, $2 per 1M output tokens
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)

            prompt_cost = (prompt_tokens / 1_000_000) * 1.0
            completion_cost = (completion_tokens / 1_000_000) * 2.0

            total_cost = prompt_cost + completion_cost
            self.total_cost += total_cost
