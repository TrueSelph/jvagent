---
name: answer
description: >-
  Answer knowledge questions by searching internal sources and the web
  in a structured cascade. Cite sources. Signal uncertainty when
  relying on parametric knowledge. Say "I don't know" when nothing
  is found.
requires-actions:
  - PageIndexAction
  - SerperWebSearchAction
allowed-tools:
  - search
response-mode: respond
version: 1
tags:
  - rag
  - retrieval
  - knowledge
  - informational
---

## Workflow

1. Classify the question's knowledge domain and whether it likely needs internal KB, external web, or general knowledge.
2. Search internal PageIndex first using `answer__search` with `source="pageindex"` (default). If strong results are returned, synthesize from those and cite with `[PageIndex: <doc_name>]`.
3. If PageIndex results are weak, missing, or incomplete, supplement with `answer__search` using `source="web"`. Cite web results with `[Web: <title>](<link>)`.
4. If the question needs both perspectives (internal + external), use `source="all"` in a single call.
5. If user profile or memory context appears in conversation directives (injected by the pipeline), incorporate it naturally. Do not mention "profile", "memory", or "long-term storage" — just weave the facts in as your own understanding.
6. **Uncertainty signaling** (critical):
   - If retrieval returned strong, relevant evidence → answer confidently with citations.
   - If retrieval returned partial or tangential evidence → answer with qualifications, flag the gaps.
   - If retrieval returned nothing useful **but** you have relevant parametric knowledge → preface with: *"I may not be quite sure, but..."* then provide the parametric answer. Label the section as `[General Knowledge]`.
   - If retrieval returned nothing and you have no reliable parametric knowledge → say: *"I don't know"* or *"I couldn't find reliable information on that."*
7. Never fabricate citations, links, document names, or quoted text.
8. Provenance labeling: always tag each factual claim with its source — `[PageIndex]`, `[Web]`, or `[General Knowledge]`.

## Constraints

- Always search PageIndex before web. Web search is a supplement, not the first choice.
- Use targeted queries. Broad queries waste retrieval budget.
- If the first query returns poor results, rephrase with more specific terms before escalating.
- When searching the web, prefer concise queries (2-5 words) for best results.
- Do not make multiple identical searches — if a query fails, change it.

## Scope

This skill is for answering knowledge-seeking questions using a structured retrieval cascade. Use it when the user asks a factual, conceptual, or informational question that may require looking up internal or external sources. Do not use it for transactional tasks (sending email, creating events, modifying files) or general conversation that does not need evidence-backed answers.

## Grounding

- Only cite document names, snippets, references, titles, and links that were returned by the `answer__search` tool.
- If retrieval returns low-quality or empty results, state that limitation explicitly before escalating or falling back to parametric knowledge.
- Never invent document titles, reference paths, URLs, or quoted text that do not appear in search results.
- When relying on parametric knowledge (no retrieval results), always signal uncertainty with the prescribed phrasing.