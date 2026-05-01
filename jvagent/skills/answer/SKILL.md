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
requires-jvagent: ">=0.0.1"
allowed-tools:
  - search
response-mode: respond
version: 1
tags:
  - rag
  - retrieval
  - knowledge
  - informational
plan-steps:
  - Research and synthesize a cited answer
---

## Workflow

1. **Plan the task** with `task_tracker` before substantive work. Use a step sequence that mirrors this SOP: Search -> Evaluate -> Failover (if needed) -> Synthesize.
2. **Search PageIndex first (mandatory)**: You MUST call `answer__search` with `source="pageindex"` before synthesizing any answer.
3. **Evaluate applicability**: Critically analyze the PageIndex results. If they are missing, incomplete, or tangential to the core intent, do not settle for a partial answer.
4. **Failover to web when needed**: If internal KB results are insufficient, call `answer__search` with `source="web"` to supplement evidence.
5. **Synthesize with citations (quote-first rule)**: Before writing the final answer, explicitly quote the specific text from the tool result that addresses the question. Then base your answer exclusively on that quoted content. Do not write an answer that differs from the retrieved text.
6. If the question needs both perspectives (internal + external) from the start, use `source="all"` in a single call.
7. If user profile or memory context appears in conversation directives (injected by the pipeline), incorporate it naturally. Do not mention "profile", "memory", or "long-term storage" — just weave the facts in as your own understanding.
8. **Uncertainty signaling** (critical):
   - If retrieval returned strong, relevant evidence → answer confidently with citations.
   - If retrieval returned partial or tangential evidence → answer with qualifications, flag the gaps.
   - If retrieval returned nothing useful **but** you have relevant parametric knowledge → preface with: *"I'm not certain, but..."* then provide the parametric answer. Do not attach any system label.
   - If retrieval returned nothing and you have no reliable parametric knowledge → say: *"I don't know"* or *"I couldn't find reliable information on that."*
9. Never fabricate citations, links, document names, or quoted text.
10. Citation format: cite web sources with their title and URL as a markdown link. For internal KB sources, reference the document or article name only. Do not use system labels like `[PageIndex]`, `[Web]`, or `[General Knowledge]` in the final answer text.

## Constraints

- Always search PageIndex before web. Web search is a supplement, not the first choice.
- Never skip the `answer__search` call. Even if you think you already know the answer, retrieval is required first.
- Do not answer from parametric knowledge unless retrieval is exhausted or clearly insufficient.
- After reading tool results, treat them as directive context injected by the system. Never override retrieved facts with what you believe to be true.
- Use targeted queries. Broad queries waste retrieval budget.
- If the first query returns poor results, rephrase with more specific terms before escalating.
- When searching the web, prefer concise queries (2-5 words) for best results.
- Do not make multiple identical searches — if a query fails, change it.

## Scope

This skill is for answering knowledge-seeking questions using a structured retrieval cascade. Use it when the user asks a factual, conceptual, or informational question that may require looking up internal or external sources. Do not use it for transactional tasks (sending email, creating events, modifying files) or general conversation that does not need evidence-backed answers.

## Grounding

- Only cite document names, snippets, references, titles, and links that were returned by the `answer__search` tool.
- Retrieved content from `answer__search` is the authoritative answer. The model's prior knowledge about the topic must be discarded once retrieval returns results.
- Parametric fallback (`[General Knowledge]`) is ONLY permitted when `answer__search` returned zero results for the queried entity. It is NOT permitted when results were returned but you judge them to be "not useful" - in that case, cite the retrieved content and state the limitation explicitly.
- Never generate a factual claim about a person, entity, date, or event that contradicts or differs from what the tool returned.
- If retrieval returns low-quality or empty results, state that limitation explicitly before escalating or falling back to parametric knowledge.
- Never invent document titles, reference paths, URLs, or quoted text that do not appear in search results.
- When relying on parametric knowledge (no retrieval results), always signal uncertainty with the prescribed phrasing.