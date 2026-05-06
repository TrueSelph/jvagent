"""Serper web search action.

Implements web search using Serper's Google Search REST API.
"""

import http.client
import json
import logging
from typing import Any, Dict, List
from urllib.parse import urlparse

from jvspatial.core.annotations import attribute
from jvspatial.env import env

from jvagent.action.web_search.base import BaseWebSearchAction

logger = logging.getLogger(__name__)
_SERPER_DEFAULT_ENDPOINT = "https://google.serper.dev/search"


class SerperWebSearchAction(BaseWebSearchAction):
    """Web search action using the Serper API.

    Uses stdlib ``http.client`` to call Serper's REST API directly.
    API docs: https://serper.dev/search

    Configuration:
        SERPER_API_KEY in ``.env``
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
        default=5, description="The maximum number of results to return"
    )

    @staticmethod
    def _env_api_key() -> str:
        return env("SERPER_API_KEY")

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

        api_key = (self._env_api_key() or "").strip()
        headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}

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

    async def get_tools(self) -> List[Any]:
        from jvagent.tooling.tool import Tool

        action = self

        async def _search(query: str, limit: int = 5) -> str:
            import json

            results = await action.search(query, max_results=limit)
            if not results:
                return "No web search results found."
            return json.dumps(results, indent=2)

        return [
            Tool(
                name="web_search__search",
                description="Search the public web for current information. Returns titles, links, and snippets.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max results to return (default 5).",
                            "default": 5,
                        },
                    },
                    "required": ["query"],
                },
                execute=_search,
            ),
        ]
