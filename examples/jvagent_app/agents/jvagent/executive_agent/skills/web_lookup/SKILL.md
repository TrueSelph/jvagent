---
name: web_lookup
description: Look up a person, company, or current fact on the public web and summarize what's found.
allowed-tools:
  - web_search__search
tags:
  - research
  - lookup
---

# Web Lookup — Standard Operating Procedure

Use this procedure when the user asks "who is X", "what is X", or anything that
needs current public information you don't already know.

1. Call `web_search__search` with a focused query built from the user's request
   (the person/company/topic plus any disambiguating context).
2. If the first results are thin or ambiguous, run **one** refined search with
   more specific terms — do not loop more than twice.
3. Synthesize a short, factual answer from the snippets. Cite what the sources
   say; do not assert details the snippets don't support.
4. If nothing relevant is found, say so plainly and ask the user for a
   disambiguating detail (organization, location, role) — do not guess.

Keep the final answer concise: who/what it is, the 1–2 most relevant facts, and
a note if confidence is low.
