# ADR 0042 — Authoritative SESSION CONTEXT in the Orchestrator prompt

**Status**: Accepted
**Date**: 2026-07-27
**Relation**: Extends the CURRENT CHANNEL ground-truth pattern
([`loop.py`](../../jvagent/action/orchestrator/loop.py)). Complements
`get_current_datetime` in [`core_tools.py`](../../jvagent/action/orchestrator/core_tools.py).
Aligns with [thin-harness](../../docs/thin-harness.md) (environment facts ≠
prep steering).

---

## 1. Context

Relative time in user utterances (“this year”, “today”) was resolved from model
training memory when the model skipped `get_current_datetime`. Tool-only clock
access is insufficient for always-on awareness. The harness already injects
**CURRENT CHANNEL** as turn-stable ground truth so the model does not invent
where the user is — clock belongs in that same class.

## 2. Decision

Every Orchestrator turn injects a **SESSION CONTEXT** block into the system
prompt (once in `_prepare_turn`, via `{session_context_section}` immediately
after identity):

- `CURRENT DATE/TIME` + ISO 8601 from `App.now()` (app timezone when set)
- `CURRENT CHANNEL` when `visitor.channel` is set (folded out of the skills
  section prepend)
- Fixed authority line: relative time must use this clock, never a training
  cutoff

`get_current_datetime` remains for mid-turn refresh only.

No YAML knobs. No utterance heuristics. No auto tool call.

## 3. Consequences

- “This year” resolves to the live calendar year without a tool round-trip.
- Slightly larger cacheable system prefix (~4–6 lines) every turn.
- Custom `system_prompt` without `{session_context_section}` still gets the
  block appended (legacy fallback).

## 4. Alternatives considered

- Auto-call `get_current_datetime` on tick 0 — rejected (extra tick cost;
  still optional if the model ignores the observation).
- Config toggle to disable injection — rejected (framework law; same as
  channel ground truth).
- Operator `system_prompt_extra` with a frozen date — rejected (stale;
  consumer-specific).
