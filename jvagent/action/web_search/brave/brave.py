"""Brave Search web search action.

Implements web search using Brave's Search REST API.
"""

import logging
from typing import Any, Dict, List

import httpx
from jvspatial.core.annotations import attribute

from jvagent.action.web_search.base import BaseWebSearchAction

logger = logging.getLogger(__name__)

_BRAVE_DEFAULT_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


class BraveWebSearchAction(BaseWebSearchAction):
    """Web search action using the Brave Search API.

    Uses ``httpx`` to call Brave's REST API directly.
    API docs: https://api.search.brave.com/app/documentation/web-search

    Configuration:
        api_key: Your Brave Search API subscription token
        api_endpoint: Brave Search API endpoint URL
        country: Two-letter country code for result localization (default: GY)
        search_lang: Language for search results (default: en)
        ui_lang: User interface language (default: en-US)
        safesearch: SafeSearch filter level — off, moderate, strict (default: moderate)
        max_results: Maximum number of results to return
    """

    api_endpoint: str = attribute(
        default=_BRAVE_DEFAULT_ENDPOINT,
        description="Brave Search API endpoint URL",
    )
    country: str = attribute(
        default="GY",
        description="Two-letter country code for result localization (e.g., US, GB, GY)",
    )
    search_lang: str = attribute(
        default="en",
        description="Language code for search results (e.g., en, es, fr)",
    )
    ui_lang: str = attribute(
        default="en-US",
        description="User interface language for the response (e.g., en-US)",
    )
    safesearch: str = attribute(
        default="moderate",
        description="SafeSearch filter level: off, moderate, or strict",
    )

    async def search(self, query: str, **kwargs: Any) -> List[Dict[str, str]]:
        """Execute a Brave Search query and return normalized results.

        Args:
            query: The search query string
            **kwargs: Additional Brave API parameters (override instance defaults)

        Returns:
            List of result dicts with keys: title, link, snippet
        """
        params: Dict[str, Any] = {
            "q": query,
            "count": kwargs.get("count", self.max_results),
            "country": kwargs.get("country", self.country),
            "search_lang": kwargs.get("search_lang", self.search_lang),
            "ui_lang": kwargs.get("ui_lang", self.ui_lang),
            "safesearch": kwargs.get("safesearch", self.safesearch),
        }

        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": self.api_key,
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout)) as client:
                response = await client.get(
                    self.api_endpoint, params=params, headers=headers
                )
                response.raise_for_status()
                data = response.json()

            web_results = data.get("web", {}).get("results", [])
            logger.debug(
                f"BraveWebSearchAction: Found {len(web_results)} results for query: {query!r}"
            )

            normalized = [
                {
                    "title": r.get("title", ""),
                    "link": r.get("url", ""),
                    "snippet": r.get("description", ""),
                }
                for r in web_results[: self.max_results]
            ]

            self.total_requests += 1
            self.total_results += len(normalized)

            return normalized

        except httpx.HTTPStatusError as e:
            logger.error(
                f"BraveWebSearchAction: HTTP error {e.response.status_code} for query {query!r}: {e}",
                exc_info=True,
            )
            return []
        except httpx.TimeoutException as e:
            logger.error(
                f"BraveWebSearchAction: Request timed out for query {query!r}: {e}",
                exc_info=True,
            )
            return []
        except Exception as e:
            logger.error(
                f"BraveWebSearchAction: Search failed for query {query!r}: {e}",
                exc_info=True,
            )
            return []
