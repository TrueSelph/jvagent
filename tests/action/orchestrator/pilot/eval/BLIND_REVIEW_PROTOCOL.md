# Fixed-Evidence Blind Review Protocol

The automated scorer is a diagnostic gate for output shape, phrase coverage,
source IDs, and quote occurrence. A passing score does **not** qualify factual
support, calibration, or harness quality. Every candidate response must receive
independent human review before it contributes to a driver-level qualification
claim.

## Review procedure

1. The evaluation custodian creates paired packets with
   `prepare_blind_review()`. Reviewers receive the prompt, the same fixed source
   text, and randomized candidate labels. They must not receive driver, model,
   provider, run telemetry, or the answer key.
2. Two reviewers independently score each candidate from 0 to 2 on evidence
   support, citation coverage, calibration, and task adherence. Score each
   material finding, then assign the case-level rubric rating. Keep the
   supporting source and claim location in each rationale.
3. Evidence support requires the source to entail the claim at its full scope.
   An exact quote can still fail when the claim reverses negation, adds
   causality, generalizes beyond the cited population, drops a condition, or
   changes a date, quantity, unit, or degree of certainty. Quote occurrence is
   a separate mechanical check and never substitutes for this judgment.
4. Mark a critical failure when an answer contains a prohibited claim, invents
   a value, hides an explicit conflict, or presents unsupported material as
   established. Record the exact claim and source evidence. A critical failure
   is not averaged away by high scores on other dimensions.
5. Preserve both initial annotations. A third reviewer adjudicates every
   dimension disagreement and every disagreement about a critical failure.
   Record the adjudicated value and rationale separately; never overwrite the
   independent ratings.
6. Report per-dimension exact agreement before adjudication, the number and
   type of disagreements, critical-failure agreement, and the adjudication
   outcome. If the sample is too small for a meaningful agreement statistic,
   state that limitation instead of implying reliability.

## Qualification boundary

No candidate or driver is semantically qualified until the paired outputs have
complete independent ratings and all disagreements have been adjudicated.
Report model/provider, source revision, manifest revision, replicate count,
blinded review coverage, provider-reported versus estimated usage, errors, and
latency separately. The scorer's `passed` field remains for compatibility and
means only that deterministic checks passed; `fully_qualified` remains false
until an external qualification process considers the review and operational
evidence together.
