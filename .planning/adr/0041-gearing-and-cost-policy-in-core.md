# ADR 0041 — Gearing and cost policy fixed in Orchestrator core

**Status**: Accepted
**Date**: 2026-07-27
**Supersedes**: Config-surface decisions in
[ADR-0039](0039-gearing-escalate-after-first-tool.md) and
[ADR-0040](0040-planning-and-sticky-finalize-gear.md) (policy remains; knobs
removed). Extends [ADR-0016](0016-model-gearing-light-heavy.md).

---

## 1. Context

ADR-0039 / ADR-0040 encoded the right gearing and finalize behavior as YAML
attributes (`escalate_after_tool_calls`, `escalate_on_skill`,
`escalate_on_planning`, `sticky_finalize_gear`) plus related cost toggles
(`skip_compose_without_guidance`, `include_history_events`,
`history_max_statement_length`). Defaults already matched the intended law;
exposing them proliferated framework knobs without a product need.

jvagent is a reusable framework: ship one opinionated loop policy, not a dial
board for every measured default.

## 2. Decision

**Hard-code** the following in Orchestrator core (no `attribute`):

| Policy | Behavior |
|---|---|
| Gearing (when `light_model` set) | skill active → heavy; `planning: true` → heavy from tick 0; ≥1 substantive tool → heavy; else light |
| Finalize | always `last_gear` (never force light wrap-up) |
| Bare egress compose | always skip when no model-facing guidance / shaping |
| Loop history | untruncated statement length; `[EVENT]` lines never included |

**Keep as product config** (opt-in features / sizing, not policy toggles):

- `light_model*` — engages gearing
- `planning` — surfaces `update_plan`
- `history_limit`, `max_statement_length` — window / reply soft-cap
- `model*` / `reasoning_*` — heavy profile

Transient ack complexity stays independent: arm only on skill or ≥2 substantive
tools (single-tool turns stay silent).

## 3. Consequences

- Consumers cannot opt into stickier light (`escalate_after_tool_calls: 2`) or
  light finalize; that path is retired.
- Agent YAML shrinks to `light_model` + `planning` for gearing/plan engagement.
- Docs describe the law, not a 7-knob panel.

## 4. Alternatives considered

- Keep knobs as rare escape hatches — rejected (proliferation; unused in
  production YAML).
- New `gearing_policy` enum — rejected (still a dial; same opinion as code).
