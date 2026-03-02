"""Serper web search action.

Implements web search using Serper's Google Search REST API.
"""

import http.client
import json
import logging
from typing import Any, Dict, List
from urllib.parse import urlparse

from jvspatial.core.annotations import attribute

from jvagent.action.web_search.base import BaseWebSearchAction

logger = logging.getLogger(__name__)
_SERPER_DEFAULT_ENDPOINT = "https://google.serper.dev/search"


class SerperWebSearchAction(BaseWebSearchAction):
    """Web search action using the Serper API.

    Uses stdlib ``http.client`` to call Serper's REST API directly.
    API docs: https://serper.dev/search

    Configuration:
        api_key: Your Serper API subscription token
        api_endpoint: Serper API endpoint URL
        gl: Two-letter country code for result localization (default: gy)
        hl: Language for search results (default: en)
        max_results: the maximum number of results to return
    """

    api_endpoint: str = attribute(
        default=_SERPER_DEFAULT_ENDPOINT,
        description="Serper API endpoint URL",
    )
    gl: str = attribute(
        default="gy",
        description="Two-letter country code for result localization (e.g., US, GB, GY)",
    )
    hl: str = attribute(
        default="en",
        description="Language code for search results (e.g., en, es, fr)",
    )
    engine: str = attribute(
        default="google",
        description="Search engine to use (e.g., google_light, google_news)",
    )
    max_results: int = attribute(
        default=5,
        description="The maximum number of results to return"
    )

    async def search(self, query: str, **kwargs: Any) -> List[Dict[str, str]]:
        """Execute a Google search via Serper and return normalized results.

        Args:
            query: The search query string
            **kwargs: Additional Serper parameters (override instance defaults)

        Returns:
            List of result dicts with keys: title, link, snippet
        """
        parsed = urlparse(self.api_endpoint)
        host = parsed.netloc or "google.serper.dev"
        path = parsed.path or "/search"

        payload = {
            "q": query,
            "gl": kwargs.get("gl", self.gl),
            "hl": kwargs.get("hl", self.hl),
            "engine": kwargs.get("engine", self.engine),
        }

        headers = {"X-API-KEY": self.api_key, "Content-Type": "application/json"}

        try:
            conn = http.client.HTTPSConnection(host)
            conn.request("POST", path, json.dumps(payload), headers)
            res = conn.getresponse()
            data = res.read()
            conn.close()

            data_dict = json.loads(data.decode("utf-8"))
            organic = data_dict.get("organic", [])
            logger.debug(
                f"SerperWebSearchAction: Found {len(organic)} organic results for query: {query!r}"
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
                f"SerperWebSearchAction: Search failed for query {query!r}: {e}",
                exc_info=True,
            )
            return []
