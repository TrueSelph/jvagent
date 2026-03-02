# Web Search Retrieval Interact Action

An `InteractAction` that performs web searches via a configured `BaseWebSearchAction` and injects the results as a directive into the conversation.

## Overview

`WebSearchRetrievalInteractAction` bridges the gap between the agent's interaction flow and external web search providers. It functions similarly to `RetrievalInteractAction` (which works with vector stores):

1.  **Extracts Query**: Uses the interaction's interpretation (or utterance as fallback) as the search query.
2.  **Resolves Search Action**: Finds a configured `BaseWebSearchAction` (e.g., Serper, SerpAPI, Brave).
3.  **Executes Search**: Calls the search action to get live results.
4.  **Injects Directive**: Formats results and adds them as a directive to the interaction, making them available to `PersonaAction`.

## Configuration

### Attributes

*   `web_search_action_type`: The class name of the search action to use (e.g., `"SerperWebSearchAction"`). If left empty, it will use the first available `BaseWebSearchAction` registered with the agent.
*   `directive`: A template for the search results directive. Must contain the `{results}` placeholder.
*   `weight`: Execution weight (default: `-75`). Should run before `PersonaAction`.
*   `anchors`: (Standard InteractAction) List of phrases or intents that trigger this action.

### Example agent.yaml Setup

```yaml
actions:
  # 1. Register a search provider
  - action: jvagent/serper_web_search
    context:
      enabled: true
      api_key: ${SERPER_API_KEY}

  # 2. Register the retrieval action
  - action: jvagent/web_search_retrieval_interact_action
    context:
      enabled: true
      web_search_action_type: SerperWebSearchAction
      anchors:
        - "User asks about current events"
        - "Search the web for..."
```

## How It Works

### Query Resolution

By default, the action prefers the interaction's `interpretation` (from InteractRouter) and falls back to `utterance`.

### Result Formatting

Results are formatted into a markdown-style list for the LLM:

```markdown
- **Page Title** (https://example.com)
  Snippet describing the content...
```

### Directive Injection

The formatted results are wrapped in a system directive:

```text
Using the following live web search results to inform your response.
Prioritize this information as it is current and up to date:

- **Specific Guyanese News** (https://news.gy/...)
  Summary of the news...
```

## Dependencies

*   Requires at least one `BaseWebSearchAction` implementation (provided by the `web_search` package) to be registered with the agent.
*   Inherits from `InteractAction`.
