"""OpenRouter model action implementation.

Provides integration with OpenRouter's API, which is OpenAI-compatible and
supports multiple language model providers through a single interface.
Supports multimodal queries (text + images) where the underlying provider supports it.
"""

import logging
from typing import Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

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
        default="https://openrouter.ai/api/v1", description="OpenRouter API endpoint URL"
    )
    model: str = attribute(
        default="openai/gpt-4o-mini",
        description="OpenRouter model identifier (provider/model format)",
    )
    http_referer: str = attribute(
        default="", description="HTTP Referer header for OpenRouter (optional)"
    )
    site_name: str = attribute(default="jvagent", description="Site name for OpenRouter (optional)")

    # OpenRouter pricing varies by model, so we don't provide defaults
    _model_pricing: Dict[str, Dict[str, float]] = attribute(private=True, default_factory=dict)

    # ============================================================================
    # Lifecycle Hooks
    # ============================================================================

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
        # OpenRouter uses OpenAI-compatible API, but we need to add headers
        # Ensure HTTP client is initialized
        await self._initialize_http_client()

        # Add OpenRouter-specific headers
        original_headers = self._http_client.headers.copy()  # type: ignore[union-attr]

        # Set OpenRouter headers
        if self.http_referer:
            self._http_client.headers["HTTP-Referer"] = self.http_referer  # type: ignore[union-attr]

        if self.site_name:
            self._http_client.headers["X-Title"] = self.site_name  # type: ignore[union-attr]

        try:
            # Call parent OpenAI implementation
            result = await super()._query(messages, tools, **kwargs)

            # Update provider name
            result.provider = "openrouter"

            return result
        finally:
            # Restore original headers
            self._http_client.headers = original_headers  # type: ignore[union-attr]

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
        await self._initialize_http_client()

        # Add OpenRouter-specific headers
        original_headers = self._http_client.headers.copy()  # type: ignore[union-attr]

        if self.http_referer:
            self._http_client.headers["HTTP-Referer"] = self.http_referer  # type: ignore[union-attr]

        if self.site_name:
            self._http_client.headers["X-Title"] = self.site_name  # type: ignore[union-attr]

        try:
            # Call parent OpenAI implementation
            result = await super()._query_stream(messages, tools, **kwargs)

            # Update provider name
            result.provider = "openrouter"

            return result
        finally:
            # Restore original headers
            self._http_client.headers = original_headers  # type: ignore[union-attr]

    # ============================================================================
    # Helper Methods
    # ============================================================================

    def _estimate_cost(self, usage: Dict[str, int]) -> None:
        """Estimate cost based on token usage.

        OpenRouter provides pricing info in the response, but for estimation
        purposes we use a generic rate if model-specific pricing isn't available.

        Args:
            usage: Usage dict with token counts
        """
        # Check if we have pricing for this model
        pricing = self._model_pricing.get(self.model)

        if pricing:
            # Use model-specific pricing
            super()._estimate_cost(usage)
        else:
            # Use generic estimation for OpenRouter
            # Approximate: $1 per 1M input tokens, $2 per 1M output tokens
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)

            prompt_cost = (prompt_tokens / 1_000_000) * 1.0
            completion_cost = (completion_tokens / 1_000_000) * 2.0

            total_cost = prompt_cost + completion_cost
            self.total_cost += total_cost

            logger.debug(f"Estimated OpenRouter cost: ${total_cost:.6f} " f"(model: {self.model})")
