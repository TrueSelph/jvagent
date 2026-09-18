# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-09-17)

**Core value:** Dependable graph-native harness — model as pilot, tools as controls, skills as flight plan.
**Current focus:** v2.0 Harness Excellence — Phases 1–5 implemented; awaiting user commit/PR

## Current Position

Phase: 5 of 5 (Operational excellence and release proof)
Plan: 3 of 3 in current phase
Status: Implementation complete; not committed
Last activity: 2026-09-18 — HP-02 … HP-12 runtime + wires

Progress: [██████████] 100%

## Performance Metrics

**Velocity:**
- Total plans completed: 13 (uncommitted)
- Average duration: —
- Total execution time: —

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1 | 2 implemented | 2 | — |
| 2 | 2 implemented | 2 | — |
| 3 | 3 implemented | 3 | — |
| 4 | 3 implemented | 3 | — |
| 5 | 3 implemented | 3 | — |

## Accumulated Context

### Decisions

- Native identity only; no host product concepts in core
- Snapshots, never live host imports into the Orchestrator
- Authority server-side, never in model payloads
- HostCapabilityProvider methods are async
- `turn_cache` ContextVar kept
- Fake host fixture, not Integral
- Same-session policy is **lease**
- TurnRun is a journal Object (I-GRAPH-02), not a conversation Node
- JSON/SQLite active-active is unsupported
- Subprocess ≠ sandbox

### Pending Todos

User will commit and open PR.

### Blockers/Concerns

- Durable outbox transport swap still deferred (HarnessStore is the first backend)
- Active-active for Mongo/Postgres marked degraded until Redis/Dynamo leases are configured

## Deferred Items

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| Transport | First durable outbox/coordination transport | Future | v2.0 start |
| Retention | Separate checkpoint vs event retention per backend | Future | v2.0 start |
| Workers | Background work vs TurnRun executor | Future | v2.0 start |
| Skills | External publisher registry + revocation service | Future | v2.0 start |

## Session Continuity

Last session: 2026-09-18
Stopped at: Phases 2–5 implemented (HP-02 … HP-12)
Resume file: None

Next: user commit + PR
