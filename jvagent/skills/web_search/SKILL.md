---
name: web_search
description: Search the public web for supplemental/current information after internal retrieval.
requires-actions:
  - SerperWebSearchAction
allowed-tools:
  - web_search__search
version: 1
tags:
  - web_search__search
  - retrieval
---

## Workflow

1. Determine whether PageIndex/internal sources are sufficient first.
2. Use this skill only when the request needs fresh external information or internal retrieval is incomplete.
3. Use the `web_search__search` tool with a concise, targeted query.
4. Review returned results (title, link, snippet) and synthesize supplemental findings.
5. Cite sources by including links from the results.

### Constraints

- Prefer specific queries over broad ones for better results.
- When multiple searches are needed, narrow each query to a distinct aspect.
- If no useful results are returned, answer from existing knowledge and note the limitation.

## Scope

This skill is for current-information retrieval from the public web. Use it as a fallback/supplement when freshness or external sources are needed, or when `pageindex_search` does not provide enough coverage. Do not use it as first choice for internal-only indexed content.

## Grounding

- Only cite titles, snippets, and URLs that were returned by the search tool.
- If search yields weak or empty results, state that and avoid fabricated claims.
- Do not invent links, publication names, or dates not present in retrieved results.