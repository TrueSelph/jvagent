"""Base class for web search actions.

All web search action implementations must inherit from BaseWebSearchAction
and implement the `search` method to return a normalized list of results.
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action

logger = logging.getLogger(__name__)


class BaseWebSearchAction(Action, ABC):
    """Abstract base class for web search actions.

    Concrete implementations (e.g., ``SerpAPIWebSearchAction``,
    ``BraveWebSearchAction``) extend this class and implement ``search``
    to call their respective search API, returning a normalized result list.

    Each result dict contains:
        title: Page title
        link: Page URL
        snippet: Short description / excerpt

    Common Configuration:
        api_key: Authentication key for the provider API
        timeout: Request timeout in seconds
        max_results: Maximum number of results to return

    Usage (agent.yaml):
        Register a concrete provider action at the agent level. Then point
        ``WebSearchRetrievalInteractAction.web_search_action_type`` at its
        class name (e.g., ``"SerpAPIWebSearchAction"``).
    """

    api_key: str = attribute(default="", description="API key for the provider")
    timeout: int = attribute(default=30, description="Request timeout in seconds", ge=1)
    max_results: int = attribute(
        default=5, description="Maximum number of search results to return", ge=1
    )

    # Cumulative metrics
    total_requests: int = attribute(default=0, description="Total search requests made")
    total_results: int = attribute(default=0, description="Cumulative results returned")

    @abstractmethod
    async def search(self, query: str, **kwargs: Any) -> List[Dict[str, str]]:
        """Execute a web search and return normalized results.

        Args:
            query: The search query string
            **kwargs: Additional provider-specific search parameters

        Returns:
            List of result dicts, each with keys: title, link, snippet
        """
        pass
