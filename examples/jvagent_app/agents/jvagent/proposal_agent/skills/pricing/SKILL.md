---
name: pricing
description: >
  Assess project pricing by extracting scope parameters from a draft
  proposal and applying a configured pricing rubric (rate card, effort
  templates, value-based adjustments) managed by PricingAction.
requires-actions:
  - PricingAction
allowed-tools:
  - pricing__extract_parameters
  - pricing__apply_pricing
  - pricing__build_investment_section
version: 1
tags:
  - pricing
  - assessment
  - proposal
---

## Workflow

1. Read the draft proposal to understand scope, timeline, team needs.
2. Use `pricing__extract_parameters` to extract structured scope parameters from the transcript and draft.
3. Use `pricing__apply_pricing` with the extracted parameters and the configured rubric name (default: "standard").
4. Review the returned PricingAssessment: line items, total, assumptions, validity.
5. Call `pricing__build_investment_section` to deterministically replace `[PRICING PLACEHOLDER]`.
6. Flag any assumptions or estimates that need client review with `[REVIEW: ...]` markers.

### Constraints

- The rubric is managed by PricingAction and must exist before calling apply_pricing.
- If no rubric name is specified, use the PricingAction's active rubric.
- Do not fabricate hours or rates — the rubric's rate card drives all calculations.
- If scope parameters are unclear, note assumptions explicitly in the assessment.
- Keep currency consistent with rubric output and investment table display.

## Scope

This skill handles all pricing-related work within a proposal, including deterministic construction of the Investment section markdown from assessment output.

## Grounding

- Only use rubrics resolved through PricingAction — never hardcode rates.
- All monetary values are in USD unless the rubric specifies otherwise.
- The assessment includes a `valid_until` date — do not extend it.
