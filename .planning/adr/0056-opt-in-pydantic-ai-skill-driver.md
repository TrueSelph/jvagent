# ADR 0056 — Opt-in Pydantic AI skill driver pilot

**Status**: Proposed
**Date**: 2026-10-04
**Relation**: Evaluates a second driver alongside [ADR-0012](0012-skill-executive-architecture.md) and [ADR-0054](0054-harness-contracts.md). It does not supersede either decision.

---

## 1. Context

JV Agent's Orchestrator currently owns model/tool iteration while Skills supply
the SOP and Actions supply domain operations. The Pydantic-inspired pilot
composes a supported Skill and its existing Action tools into a typed Pydantic
AI run. It changes which component owns the bounded model/tool loop for an
explicitly selected run, so its semantics and retirement criteria need a
decision record before the pilot can be considered qualified.

The pilot is experimental. Offline tests and browser smoke establish only the
behaviors they exercise. Provider evaluation, matched legacy comparison,
recovery qualification, and the final expansion/removal decision remain open.

## 2. Proposed decision

1. Keep `legacy` as the default `skill_runtime`; permit `capability_pilot` only
   when explicitly configured for the agent. Missing optional dependencies or
   unsupported configuration must fail with an actionable error.
2. In pilot mode, Pydantic AI owns exactly one bounded model/tool loop for the
   turn. JV Agent continues to own caller identity, skill discovery and
   admission, Action binding and authorization, TaskStore persistence, and
   ReplyAction/ResponseBus publication.
3. Keep `SKILL.md` and Action packages as the authoring and integration
   boundaries. Admit only the explicitly documented skill subset; reject
   unsupported metadata or behavior rather than silently approximating it.
4. Keep typed run state in the existing graph-backed TaskStore. Do not add a
   second state database, general workflow language, provider configuration,
   or mandatory telemetry service.
   Bound the pilot to 8 model requests, 12 Action calls, 4,000 characters per
   Action result, 20,000 total tokens, 2,000 output tokens, and 120 seconds
   per run (`PilotRunContext` defaults in
   [`contracts.py`](../../jvagent/action/orchestrator/pilot/contracts.py)).
5. Treat the pilot as a read-only research witness. Mutating operations remain
   unavailable unless a separately qualified host effect invoker provides
   approval, idempotency, receipt, and reconciliation semantics.
6. Preserve pilot snapshots when reverting to `legacy`; legacy continuation
   must ignore pilot-owned tasks. Re-entry requires current contract and
   authority validation.
7. Do not expand, release, or retire the legacy path based on this ADR. Require
   the pilot acceptance matrix, bounded live evaluation, matched comparison,
   restart/recovery evidence, and a production responsibility/bloat analysis
   before proposing that decision.

## 3. Consequences

- The experiment can test typed composition without forcing every agent to
  install Pydantic AI or changing the existing runtime default.
- Both drivers temporarily coexist, which adds code and maintenance cost. Any
  expansion proposal must identify concrete legacy responsibilities to retire;
  otherwise the pilot should be revised or removed.
- Passing schema validation does not establish factual support. Research
  quality must be evaluated against retrieved sources independently.
- No delegated-agent runtime, production write integration, or exactly-once
  external effect guarantee is implied.

## 4. Qualification references

- Implementation plan: [Pydantic-inspired skill pilot](../plans/2026-10-04-pydantic-inspired-skill-pilot.md)
- Evidence report: [pilot evidence](../reviews/2026-10-04-pydantic-inspired-pilot-evidence.md)
- Selector and implementation: `jvagent/action/orchestrator/orchestrator_interact_action.py` and `jvagent/action/orchestrator/pilot/`

This ADR remains **Proposed** until the pilot evidence is reviewed and the
user accepts a later record that adopts, revises, or rejects the architecture.
