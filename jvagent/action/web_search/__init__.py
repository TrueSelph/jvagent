"""Web search action package.

Provides `BaseWebSearchAction` and concrete provider implementations:
- ``SerpAPIWebSearchAction``  (Google via SerpAPI)
- ``BraveWebSearchAction``    (Brave Search REST API)
- ``SerperWebSearchAction``   (Google via Serper.dev REST API)

"""

from jvagent.action.web_search.base import BaseWebSearchAction
from jvagent.action.web_search.brave import BraveWebSearchAction
from jvagent.action.web_search.serpapi import SerpAPIWebSearchAction
from jvagent.action.web_search.serper import SerperWebSearchAction

__all__ = [
    "BaseWebSearchAction",
    "SerpAPIWebSearchAction",
    "SerperWebSearchAction",
    "BraveWebSearchAction",
]
