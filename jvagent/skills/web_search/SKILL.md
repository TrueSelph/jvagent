---
name: web_search
description: Search the web for current information and return ranked results.
requires-actions:
  - SerperWebSearchAction
allowed-tools:
  - search
version: 1
tags:
  - search
  - retrieval
---

## Workflow

1. Determine what the user needs to know that may require up-to-date or external information.
2. Use the `search` tool with a concise, targeted query.
3. Review the returned results (title, link, snippet) and synthesize an answer.
4. Cite sources by including links from the results.

### Constraints

- Prefer specific queries over broad ones for better results.
- When multiple searches are needed, narrow each query to a distinct aspect.
- If no useful results are returned, answer from existing knowledge and note the limitation.