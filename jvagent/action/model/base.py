"""Base classes for model actions.

This module provides the core abstractions for model integrations:
- BaseModelAction: Generic base class with common attributes and operations
"""

import logging
from abc import ABC
from typing import Dict, Optional

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

    async def on_register(self) -> None:
        """Called when action is registered.

        Providers should override this to initialize HTTP clients and
        validate configuration.
        """
        logger.info(f"Model action registered: {self.label} (model: {self.model})")

    async def on_disable(self) -> None:
        """Called when action is disabled.

        Providers should override this to close HTTP client connections
        and clean up resources.
        """
        logger.info(f"Model action disabled: {self.label}")
