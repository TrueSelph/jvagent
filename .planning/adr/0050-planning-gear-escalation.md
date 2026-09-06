# ADR 0050 — Planning escalates the gear on evidence, not on configuration

**Status**: Accepted
**Date**: 2026-09-06
**Relation**: Amends the gearing policy of [ADR-0041](0041-gearing-and-cost-policy-in-core.md) (and the rule stated in [ADR-0016](0016-model-gearing-light-heavy.md) §"heavy when planning is enabled"). Follow-up from [`.planning/reviews/2026-09-05-example-live-evaluation.md`](../reviews/2026-09-05-example-live-evaluation.md) §2.4.

---

## 1. Context

Gearing (ADR-0016/0041) runs a turn on the light model until it proves
multi-step: a skill activates, or a substantive tool call happens. One
exception was configuration-driven rather than evidence-driven: with
`planning: true` every tick, including tick 0 of a greeting, went to the heavy
model, on the argument that tick 0 "must reason about `update_plan`".

Measured live on the example agent (planning on, light `gpt-4.1-mini`, heavy
`gpt-4.1`): `ticks_light: 0, ticks_heavy: N, escalated: true` on every turn.
The light model was dead configuration for any agent that also wanted
resumable plans, which is most agents that run multi-step skills — the two
features cancelled each other.

## 2. Decision

`_select_gear` treats planning like every other escalation trigger:

- `planning: true` **and a plan from a prior turn is open** (the resume note
  is set for this turn) → heavy from tick 0. Resuming a plan is multi-step
  work already in evidence.
- otherwise a fresh turn starts **light**; the first substantive tool call
  escalates it, and `update_plan` is on the surface of the heavy ticks that
  follow — which is when a plan is worth recording.
- `planning_heavy_first_tick: true` (new attribute, default `false`) restores
  the previous behaviour for an operator whose light model provably fails to
  call `update_plan` on multi-step tasks.

Skill-active and ≥1-substantive-tool escalation, stickiness and the finalize
gear are unchanged.

## 3. Consequences

- Reply-only turns on a planning agent now cost the light model. On the
  example agent that is the greeting, the summary and most single-lookup turns.
- A multi-step task's tick 0 runs light. If the light model answers instead of
  calling a tool, the existing guards (grounding, act-don't-announce) deflect
  it and the turn escalates on the first real call, as for any non-planning
  agent — the model floor guidance in `docs/ORCHESTRATOR.md` applies.
- The `planning` attribute's description and the gearing docs
  (`docs/ORCHESTRATOR.md`, `configuration-keys.md`) say so; the example
  agent's comment no longer warns that its light model is unused.

## 4. Verification

`tests/action/orchestrator/test_config_surface.py`: planning alone → light at
tick 0; open plan → heavy; the knob → heavy.
