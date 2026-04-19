---
name: pageindex_search
description: Search PageIndex documents using vectorless retrieval.
requires-actions:
  - PageIndexAction
allowed-tools:
  - search
version: 1
tags:
  - retrieval
  - rag
  - pageindex
---

## Workflow

1. Determine what information the user needs that may be in the PageIndex index.
2. Use the `search` tool with a targeted query.
3. Review the returned results (title, text, summary, doc_name) and synthesize an answer.
4. Cite sources by including document names and references from the results.

### Constraints

- Prefer specific queries over broad ones for better retrieval quality.
- The default strategy is `tree_search` (LLM-guided tree reasoning), which provides the best results. Use `direct` (BM25 lexical) or `walker` (graph traversal) as alternatives.
- When searching a specific document, set `doc_name` to scope the search.
- If no useful results are returned, answer from existing knowledge and note the limitation.