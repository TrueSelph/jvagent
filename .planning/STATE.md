# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-09-17)

**Core value:** Dependable graph-native harness — model as pilot, tools as controls, skills as flight plan.
**Current focus:** v2.0 Harness Excellence — Phases 1–5 implemented; gap-close in working tree

## Current Position

Phase: 5 of 5 (Operational excellence and release proof)
Plan: 3 of 3 in current phase
Status: Implementation complete including gap-close; not committed
Last activity: 2026-09-18 — HP-02 … HP-12 runtime + wires + remaining gaps

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
- Isolation binary missing → refuse, never subprocess fallback
- Redis/Dynamo lease adapters require an explicit client

### Pending Todos

User will commit and open PR.

### Blockers/Concerns

- Real gvisor/firecracker/nsjail kernel jails are not implemented in-tree; wrap prefix + PATH check is the contract
- Active-active for Mongo/Postgres stays degraded until a shared lease backend is configured

## Deferred Items

None remaining from the v2.0 gap list. Optional later work: Redis/Dynamo *stream* transports (leases already have adapters), remote publisher service (in-process revoke/publish is the registry).

## Session Continuity

Last session: 2026-09-18
Stopped at: All listed HP gaps closed in working tree
Resume file: None

Next: user commit + PR
