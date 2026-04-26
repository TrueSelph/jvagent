---
name: proposal_draft
description: >
  Generates a structured, persuasive, and professional proposal draft
  from a client meeting transcript or written RFP context. Uses a
  Markdown specimen corpus (template.md, guide.md, past proposals)
  for structure and tone guidance. Revision markers are inserted
  for uncertain claims and items needing client review.
allowed-tools:
  - proposal_draft__retrieve_specimens
  - proposal_draft__generate_draft
version: 1
tags:
  - proposal
  - drafting
  - writing
---

## Workflow

1. Retrieve specimen documents using `proposal_draft__retrieve_specimens`.
   - Always loads `template.md` (structural skeleton) and `guide.md` (writing principles).
   - Selects up to 3 relevant past proposals from the corpus by tag-matching client context.
2. Analyze the transcript/context to identify: client, needs, scope, timeline, decision-makers, budget.
3. Call `proposal_draft__generate_draft` with the analysis, specimens, template, and guide.
4. Review the generated draft for completeness. Flag uncertain items with `[REVIEW: ...]` markers.
5. Return the structured DraftProposal summary.

### Required Structure (from template.md)

Every proposal must follow this section order:
1. Executive Summary
2. Understanding of Your Needs (restate + validate from transcript)
3. Our Approach (technical/strategic)
4. Scope of Work (deliverables, phases, out-of-scope)
5. Timeline
6. Investment (placeholder — pricing skill fills this)
7. Why Us / Differentiators
8. Next Steps

### Writing Guidelines (from guide.md)

- Be specific about the client's stated needs — quote or reference the transcript.
- Use confident, forward-looking language ("we will" not "we can").
- Quantify wherever possible.
- For uncertain claims or dates, use `[REVIEW: ...]` markers.
- Avoid buzzwords: no "synergy," "leverage," "best-in-breed."

## Scope

This skill produces only the draft text. Pricing is handled by the pricing skill, document authoring by the authoring skill. This skill's output is a DraftProposal data structure, not a formatted document.

## Grounding

- Every claim must be traceable to the transcript or RFP.
- If the transcript lacks information for a required section, note the gap with a `[REVIEW: ...]` marker — do not fabricate.
- Specimen documents are exemplars of tone and structure, not sources of fact.
