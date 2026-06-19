---
name: pageindex_search_with_references
description: >-
  Search the internal knowledge base with PageIndex and format responses with proper citations and a Sources section containing clickable links to the original documents. Use for policies, documentation, FAQs, and general knowledge base questions - not live catalog/inventory SKU lookup or real-time database queries.
requires-actions:
  - PageIndexAction
allowed-tools:
  - pageindex__search
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
  - policies
  - info
  - references
---

## When to use

Activate for **policy, company, or documentation** questions answerable from indexed documents (e.g., FAQ files, handbooks, policy guides):

- **Payments & Billing:** payment methods, billing terms, bank/transfer details
- **Shipping, Returns & Policies:** processing times, shipping rates, return/exchange policies, terms of service
- **Hours & Locations:** operational hours, office/store locations, delivery zones
- **General Support & Operations:** contact channels, warranties, account setup, company history, general "how does X work" questions

Typical triggers: "what are your hours", "how do I pay", "return policy", "where are you located", "do you deliver to...", "warranty", "layaway", "how long does shipping take".

**Do not use this skill when:**

- The user requests **live/real-time inventory** or transactional status (e.g., price, stock, SKU lookup, live order status tracking) that requires catalog/database tools.
- The question requires detailed product/technical specifications from external sources not present in the indexed documents.
- The task is conversational or transactional in nature and does not require document retrieval.

## Activation

Call `use_skill` with `skill_name: "pageindex_search_with_references"` before calling `pageindex__search`. Always pass `include_references: true` on `pageindex__search` calls so source citations are included. You have no independent knowledge of the internal policies or documents — every fact must come from search results returned this turn.

## First action

Short or vague queries from the user (e.g., "hours?", "how to pay?") are weak retrieval queries. Before searching:

1. Infer the target topic (e.g., payments, shipping, returns, support, locations).
2. Rephrase into a concrete, descriptive `query` string optimized for keyword/vector search (e.g. "store opening hours opening times", "accepted payment methods billing policies", "return exchange policy refund terms").
3. As your **first tool call**, run `pageindex__search` with `include_references: true` and the rephrased query. Set `doc_name` to the specific document if the document scope is known.
4. Synthesize a direct answer from the retrieved excerpts. Each result is tagged with a reference number `[N]` and followed by a references block listing cited sources. Cite `[N]` inline when you use a fact from an excerpt, and list only the references you actually cited at the end of your response. Re-issue search only with a **refined** query — never repeat an identical search. At most **two** searches per turn (second pass only if the first is empty or off-topic).

Prefer `strategy: tree_search` for natural-language policy/FAQ questions. If results are thin, try one second search with a narrower/broader rephrase or `strategy: direct` before stating a coverage gap.

## Response shape

After retrieval:

1. **Answer** — 2–5 short, clear sentences grounded in the retrieved excerpts. Cite `[N]` inline for each fact drawn from an excerpt. Place the citation **immediately after** the claim or sentence it supports.
2. **References / Sources** — at the end, list only the `[N]` reference lines you actually cited (copy them verbatim from the references block). Do not paraphrase or reorder them. If no sources were cited, omit this section entirely.
3. **Contact / Support** — when relevant (hours, location, policy help, returns), include official contact details (phone, email, WhatsApp, website) **only as returned** in the search results.
4. **Next step** — one low-friction next step (e.g., advising them to contact support, visit a link) when it fits; do not invent links or phone numbers.
5. **No catalog / transaction tools** — do not call transaction/inventory tools on document retrieval turns.

### Constraints

- Prefer specific `query` strings (topic + intent) over copying the user's message verbatim when it is vague.
- Scope searches to target documents (using `doc_name`) when the relevant document name is known.
- **Pricing & Numbers:** Quote amounts, currency, and fees exactly as returned in the search results. Do not calculate taxes, discounts, or totals yourself.
- **Availability & Claims:** Do not make definitive claims about stock, status, or approvals unless explicitly backed by the search results.
- If no useful results: say the documentation did not cover it, avoid guessing, and offer default contact channels from prior results or ask one clarifying question.

## Workflow

1. Treat PageIndex as the **primary** source for policy, FAQ, and document-backed answers.
2. Use `pageindex__search` with a targeted, rephrased `query`.
3. Review results and answer from those nodes first.
4. If coverage is missing or unclear, state the gap explicitly; do not invent policies, account details, or timelines.
5. Do not use document ingestion or broad web research from this skill.

## Scope

- **In scope:** policies, company info, operational guides, support paths, and general FAQs documented in the indexed knowledge base.
- **Out of scope:** live catalog search, transactional database queries, objection handling/salesmanship, product-detail specs not present in retrieved nodes, document ingestion, and broad web research.

## Grounding

- Only cite document names, snippets, and references that `pageindex__search` returned in the directive excerpts and references block.
- Never invent handbook titles, reference paths, account numbers, or quoted policy text.
- Do not claim availability, live price, or policy details unless they appear in search results this turn.
