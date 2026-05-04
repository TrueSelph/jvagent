---
name: pageindex_search
description: >-
  Knowledge retrieval from the agent's indexed knowledge base: answer knowledge-based questions using ingested documents (FAQs, manuals, policies, runbooks, wikis, notes, exports—whatever has been indexed). Use whenever the user needs **domain-specific** answers instead of general model knowledge. Do not use for document upload/delete/list lifecycle (that is `pageindex_docs`); use another skill when the task is explicitly handled elsewhere (e.g. live tools, APIs, or workflows bound to a different skill).
requires-actions:
  - PageIndexAction
allowed-tools:
  - pageindex_search__search
version: 3
tags:
  - knowledge
  - documentation
  - retrieval
  - rag
  - pageindex
  - indexed
  - internal
  - faq
  - help
---

## Workflow

1. Treat PageIndex as the primary knowledge source for internal/domain questions.
2. Use the `pageindex_search__search` tool with a targeted query and, when possible, constrain by `doc_name`.
3. Review returned results (`doc_name`, title, text, summary, references) and answer from those first.
4. If coverage is missing or unclear, state the gap explicitly and only then propose supplemental web search.
5. Cite sources by including document names and references from PageIndex results.

### Constraints

- Prefer specific queries over broad ones for better retrieval quality.
- The default strategy is `tree_search` (LLM-guided tree reasoning), which provides the best results. Use `direct` (BM25 lexical) or `walker` (graph traversal) as alternatives.
- When searching a specific document, set `doc_name` to scope the search.
- If no useful results are returned, answer from existing knowledge and note the limitation.

## Scope

This skill is for retrieving information from indexed PageIndex documents via search. Use it as the first retrieval step when questions may be answered by internal knowledge. Do not use it for document ingestion/deletion tasks (use `pageindex_docs`) or broad internet research unless internal retrieval is insufficient.

## Grounding

- Only cite document names, snippets, and references that the `pageindex_search__search` tool returned.
- If retrieval returns low-quality or empty results, state that limitation explicitly before proposing external supplementation.
- Never invent document titles, reference paths, or quoted text that do not appear in search results.