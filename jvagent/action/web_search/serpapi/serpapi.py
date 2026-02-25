"""SerpAPI web search action.

Implements web search using SerpAPI's Google Search integration.
"""

import logging
from typing import Any, Dict, List

from jvspatial.core.annotations import attribute

from jvagent.action.web_search.base import BaseWebSearchAction

logger = logging.getLogger(__name__)


class SerpAPIWebSearchAction(BaseWebSearchAction):
    """Web search action using the SerpAPI Google Search integration.

    Uses the ``google-search-results`` library to query Google via SerpAPI.

    Configuration:
        api_key: Your SerpAPI API key
        engine: Search engine to use (default: google_light)
        location: Geographic location bias for results
        google_domain: Google domain to use (default: google.com)
        hl: Interface language code (default: en)
        gl: Country code (default: gy)
        max_results: Maximum number of organic results to return
    """

    engine: str = attribute(
        default="google_light",
        description="SerpAPI search engine (e.g., google, google_light, bing)",
    )
    location: str = attribute(
        default="Guyana",
        description="Geographic location to bias search results",
    )
    google_domain: str = attribute(
        default="google.com", description="Google domain to use for searches"
    )
    hl: str = attribute(
        default="en", description="Interface language code (e.g., en, es, fr)"
    )
    gl: str = attribute(
        default="gy", description="Country code for search results (e.g., us, gy, gb)"
    )

    async def search(self, query: str, **kwargs: Any) -> List[Dict[str, str]]:
        """Execute a Google search via SerpAPI and return normalized results.

        Args:
            query: The search query string
            **kwargs: Additional SerpAPI parameters (override instance defaults)

        Returns:
            List of result dicts with keys: title, link, snippet
        """
        try:
            from serpapi import GoogleSearch
        except ImportError:
            logger.error(
                "SerpAPIWebSearchAction: 'google-search-results' package is not installed. "
                "Add it to your dependencies: pip install google-search-results>=2.4.2"
            )
            return []

        params = {
            "engine": kwargs.get("engine", self.engine),
            "q": query,
            "location": kwargs.get("location", self.location),
            "google_domain": kwargs.get("google_domain", self.google_domain),
            "hl": kwargs.get("hl", self.hl),
            "gl": kwargs.get("gl", self.gl),
            "api_key": self.api_key,
        }

        try:
            search = GoogleSearch(params)
            results = search.get_dict()

            organic = results.get("organic_results", [])
            logger.debug(
                f"SerpAPIWebSearchAction: Found {len(organic)} organic results for query: {query!r}"
            )

            normalized = [
                {
                    "title": r.get("title", ""),
                    "link": r.get("link", ""),
                    "snippet": r.get("snippet", ""),
                }
                for r in organic[: self.max_results]
            ]

            self.total_requests += 1
            self.total_results += len(normalized)

            return normalized

        except Exception as e:
            logger.error(
                f"SerpAPIWebSearchAction: Search failed for query {query!r}: {e}",
                exc_info=True,
            )
            return []
