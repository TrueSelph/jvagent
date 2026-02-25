# Web Search Action Package

Provides a standardized interface and multiple provider implementations for web search capabilities.

## Overview

The `web_search` package provides the core abstractions and concrete implementations for web search providers. It follows a provider pattern similar to the `action/model` package, separating the search implementation from the interaction logic.

### Key Components

1.  **`BaseWebSearchAction`**: Abstract base class defining the search interface.
2.  **`SerperWebSearchAction`**: Concrete implementation using the [Serper.dev](https://serper.dev) API.
3.  **`BraveWebSearchAction`**: Concrete implementation using the [Brave Search](https://api.search.brave.com) API.
4.  **`SerpAPIWebSearchAction`**: Concrete implementation using the [SerpAPI](https://serpapi.com) API.

## Architecture

The web search functionality is split into two distinct packages:

*   **`jvagent/web_search`** (this package): Contains the base search actions and provider-specific implementations. These are plain `Action` classes focused solely on executing searches.
*   **`jvagent/web_search_retrieval_interact_action`**: An `InteractAction` that uses a configured `BaseWebSearchAction` to perform searches based on user input and inject the results into the conversation.

## Configuration

### Base Attributes

All web search actions share these common attributes:

*   `api_key`: API key for the search provider.
*   `timeout`: Request timeout in seconds (default: 30).
*   `max_results`: Maximum number of search results to return (default: 5).

### Serper Web Search

Uses the Serper API (Google Search results).

```yaml
- action: jvagent/serper_web_search
  context:
    enabled: true
    api_key: ${SERPER_API_KEY}
    gl: "gy"  # Country code (e.g., us, gy, gb)
    hl: "en"  # Language code
```

### Brave Web Search

Uses the Brave Search API.

```yaml
- action: jvagent/brave_web_search
  context:
    enabled: true
    api_key: ${BRAVE_SEARCH_KEY}
    country: "GY"  # Two-letter country code
```

### SerpAPI Web Search

Uses the SerpAPI API.

```yaml
- action: jvagent/serpapi_web_search
  context:
    enabled: true
    api_key: ${SERPAPI_API_KEY}
    engine: "google"  # e.g., google_light, google_news
    country: "guyana"  # e.g., usa, uk, germany
    gl: "gy"  # Country code (e.g., us, gy, gb)
    hl: "en"  # Language code
```

## Usage

### Programmatic Usage

You can use a web search action directly in your code:

```python
from jvagent.action.web_search import SerperWebSearchAction

async def my_method(self, query):
    search_action = await self.get_action(SerperWebSearchAction)
    if not search_action:
        return []

    results = await search_action.search(query)
    # results = [{"title": "...", "link": "...", "snippet": "..."}]
    return results
```

### Via Retrieval Interact Action

The preferred way to use web search in an agent is via `WebSearchRetrievalInteractAction`.

```yaml
- action: jvagent/web_search_retrieval_interact_action
  context:
    enabled: true
    web_search_action_type: SerperWebSearchAction
    anchors:
      - "User asks about current events"
      - "User wants to search the web for..."
```

## Metrics

Web search actions track basic usage metrics:

*   `total_requests`: Total number of search requests made.
*   `total_results`: Cumulative number of results returned across all searches.

## Implementation Details

### Result Normalization

All providers normalize their raw API responses into a consistent format:

```python
[
    {
        "title": "Example Page Title",
        "link": "https://example.com/page",
        "snippet": "A brief description of the page content found by the search engine."
    },
    ...
]
```
