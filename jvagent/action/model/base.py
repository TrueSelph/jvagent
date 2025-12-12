"""Base classes for model actions.

This module provides the core abstractions for model integrations:
- BaseModelAction: Generic base class with common attributes and operations
"""

import logging
from abc import ABC
from typing import Dict, Optional

import httpx
from jvspatial.core.annotations import attribute

from jvagent.action.base import Action

logger = logging.getLogger(__name__)


class BaseModelAction(Action, ABC):
    """Base class for all model actions with common attributes and operations.

    This class provides the foundation for all model action types (Language Model, Embedding, etc.)
    with shared configuration, metrics tracking, and lifecycle management.

    Common Attributes:
        api_key: Provider API key for authentication
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
    api_key: str = attribute(default="", description="API key for the provider")
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

    def track_usage(self, usage: Dict[str, int], duration: Optional[float] = None) -> None:
        """Track token usage and update metrics.

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
        logger.debug(
            f"Tracked usage: {total} tokens, {duration:.3f}s (total: {self.total_tokens} tokens, "
            f"{self.total_duration:.3f}s, requests: {self.total_requests})"
        )

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
