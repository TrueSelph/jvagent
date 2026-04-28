---
name: proposal_draft
description: >
  Generates a structured, persuasive, and professional proposal draft
  from a client transcript or written RFP context. Uses a specimen
  corpus (template.md, guide.md, past proposals) for structure and tone
  guidance. Returns rendered markdown plus a structured proposal_state
  object so downstream pricing/authoring is deterministic.
allowed-tools:
  - proposal_draft__retrieve_specimens
  - proposal_draft__generate_draft
  - proposal_draft__quality_review
version: 1
tags:
  - proposal
  - drafting
  - writing
---

## Workflow

1. Retrieve specimen documents using `proposal_draft__retrieve_specimens`.
   - Always loads `template.md` (structural skeleton) and `guide.md` (writing principles).
   - Selects up to 3 relevant past proposals by tag matching and returns full text content.
2. Analyze the transcript/context to identify: client, needs, scope, timeline, decision-makers, budget.
3. Call `proposal_draft__generate_draft` with the analysis, specimens, template, and guide.
4. Call `proposal_draft__quality_review` on the generated markdown.
5. If quality checks fail, revise and rerun before handing off to pricing.
6. Return the structured proposal_state + rendered markdown package.

### Required Structure (from template.md)

Every proposal should include this section order:
1. Executive Summary
2. Understanding of Your Needs
3. Core Deliverables, Hours, Timeframes & Cost
4. Recommended Operational Costs
5. Technical Approach & Rationale
6. Client Responsibilities
7. Value Summary
8. Next Steps
9. Requirements Analysis Reference (Annex)
10. Investment (placeholder — pricing skill fills this)

### Writing Guidelines (from guide.md)

- Be specific about the client's stated needs — quote or reference the transcript.
- Use confident, forward-looking language ("we will" not "we can").
- Quantify wherever possible.
- For uncertain claims or dates, use `[REVIEW: ...]` markers.
- Avoid buzzwords: no "synergy," "leverage," "best-in-breed."
- Maintain the quality bar from the IPED specimen: explicit metadata, validity/version, detailed tables, and annex traceability.

## Scope

This skill produces rendered markdown and structured `proposal_state`. Pricing is handled by the pricing skill, and document output by the authoring skill.

## Grounding

- Every claim must be traceable to the transcript or RFP.
- If the transcript lacks information for a required section, note the gap with a `[REVIEW: ...]` marker — do not fabricate.
- Specimen documents are exemplars of tone and structure, not sources of fact.
