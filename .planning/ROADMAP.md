# Roadmap: jvagent

## Milestones

- ✅ **v1 Orchestrator** — shipped (see [`.planning/archive/EXECUTIVE-ROADMAP.md`](archive/EXECUTIVE-ROADMAP.md))
- 🚧 **v2.0 Harness Excellence** — Phases 1–5 (in progress)
- Source plan: [`docs/HARNESS_EXCELLENCE_PLAN.md`](../docs/HARNESS_EXCELLENCE_PLAN.md)

## Overview

Freeze host-neutral contracts and inventory process-local state. Then isolate identity and snapshots. Then persist turns, invocations, and delivery. Then add optional active-active plus host/skill hardening. Then prove it with traces, load, and a release record.

**Do not** claim crash-safe or active-active behavior until Phase 3 (HP-06) and Phase 4 (HP-07) evidence exists.

**Parallelism after Phase 1 freeze:** HP-02, HP-03, and HP-04 may start together. HP-08 and HP-09 run in parallel after HP-03 + HP-05 (HP-09 does **not** wait on HP-08).

## Phases

**Phase Numbering:** first GSD milestone; numbering starts at 1.

- [x] **Phase 1: Contracts and baseline** — Freeze harness contracts and inventory process-local state
- [x] **Phase 2: Identity and snapshots** — Store-backed admission and snapshot-scoped caches
- [x] **Phase 3: Durable execution** — TurnRun, invocation ledger, durable outbox
- [x] **Phase 4: Distributed runtime and extensibility** — Leases, host provider, skill hardening
- [x] **Phase 5: Operational excellence and release proof** — Trace/replay, capacity, release record

## Phase Details

### Phase 1: Contracts and baseline

**Goal**: Freeze the host-neutral identity, snapshot, TurnRun, invocation, event, and provider contracts, and attach a concrete inventory of process-local state.
**Depends on**: Nothing (first phase)
**Requirements**: CTRT-01, BASE-01
**Success Criteria** (what must be TRUE):
  1. Contract fixtures define valid and rejected transitions for TurnRun, snapshot, invocation, event, and provider APIs
  2. No host-domain field (`workspace_id`, org, App, domain schema) appears in public jvagent models or APIs
  3. Every process-local cache, lock, bus, breaker, and background registry has an owner, scope, replacement decision, and regression target
**Plans**: 2 plans

Plans:
- [x] 01-01: HP-00 Harness contract ADRs and conformance suite
- [x] 01-02: HP-01 Baseline reliability audit

### Phase 2: Identity and snapshots

**Goal**: Make `(agent_id, user_id, session_id)` the admission key and serve tools/skills only through immutable snapshots.
**Depends on**: Phase 1
**Requirements**: IDNT-01, IDNT-02, SNAP-01, SNAP-02
**Success Criteria** (what must be TRUE):
  1. Concurrent creates across workers produce one User and one Conversation
  2. Simultaneous turns on distinct sessions remain isolated; same-session policy is explicit and tested
  3. Two concurrent users/sessions receive only their own snapshots
  4. Dynamic tool/skill changes affect a new snapshot without contaminating any other caller
**Plans**: 2 plans

Plans:
- [x] 02-01: HP-02 Native identity and session admission
- [x] 02-02: HP-03 ToolSurfaceSnapshot and cache discipline

### Phase 3: Durable execution

**Goal**: Persist turn lifecycle, tool invocations, and outbound events so crashes and reconnects have an explicit story.
**Depends on**: Phase 2 (HP-04 may start against frozen Phase 1 fixtures in parallel with HP-02)
**Requirements**: RUN-01, RUN-02, INV-01, DELV-01
**Success Criteria** (what must be TRUE):
  1. A crash after tool dispatch is diagnosable; completed read tools are not repeated unnecessarily; unsafe writes require a visible recovery decision
  2. Retries reuse `invocation_id`; a duplicate dispatch cannot duplicate a supported mutating effect
  3. Reconnecting clients replay missed frames in order without duplicate rendered messages
  4. A reply created on one worker can be delivered by another (outbox, not process-local bus)
**Plans**: 3 plans

Plans:
- [x] 03-01: HP-04 TurnRun journal and resumable execution
- [x] 03-02: HP-05 Invocation ledger and idempotency adapters
- [x] 03-03: HP-06 Durable event outbox and resumable streaming

### Phase 4: Distributed runtime and extensibility

**Goal**: Optional active-active coordination, a host-neutral capability provider, and signed/isolated skill materialization.
**Depends on**: Phase 3
**Requirements**: DIST-01, DIST-02, HOST-01, HOST-02, SKIL-01, SKIL-02
**Success Criteria** (what must be TRUE):
  1. A two-worker test handles concurrent users, session contention, worker loss, and proactive delivery without lost or cross-delivered events
  2. A sample host supplies per-session dynamic tools and skills; revocation takes effect at the next snapshot; jvagent never sees the host data model
  3. Native, embedded, and remote providers pass the same invocation/revocation suite
  4. Skill activation is reproducible from digest; stale or revoked skills cannot run; untrusted scripts refuse without an approved isolation backend
**Plans**: 3 plans

Plans:
- [x] 04-01: HP-07 Active-active coordination
- [x] 04-02: HP-08 HostCapabilityProvider reference implementation
- [x] 04-03: HP-09 Skill package and execution hardening

HP-08 and HP-09 may execute in parallel. Both require HP-03 + HP-05, not each other.

### Phase 5: Operational excellence and release proof

**Goal**: An operator can explain any turn, capacity is measured, and a release artifact carries its guarantees.
**Depends on**: Phase 4 (HP-10 may start after Phase 3; HP-11 needs HP-03 + HP-06 + HP-07)
**Requirements**: OBSV-01, PERF-01, REL-01
**Success Criteria** (what must be TRUE):
  1. One correlation id reconstructs a completed or failed turn with redaction and no other user's content
  2. Representative many-user/many-session load preserves p95 targets and event ordering
  3. A release record lists artifact digest, contract versions, supported topology, evidence, limitations, and rollback path
  4. Unsupported storage/execution combinations are marked explicitly
**Plans**: 3 plans

Plans:
- [x] 05-01: HP-10 Trace, replay, and evaluation plane
- [x] 05-02: HP-11 Performance and capacity work
- [x] 05-03: HP-12 Release and compatibility evidence

## Progress

**Execution Order:**
Phases execute in numeric order. Inside a phase, plans may run in parallel when the HP dependency map allows.

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Contracts and baseline | v2.0 | 2/2 | Complete | 2026-09-17 |
| 2. Identity and snapshots | v2.0 | 2/2 | Complete | 2026-09-18 |
| 3. Durable execution | v2.0 | 3/3 | Complete | 2026-09-18 |
| 4. Distributed runtime and extensibility | v2.0 | 3/3 | Complete | 2026-09-18 |
| 5. Operational excellence and release proof | v2.0 | 3/3 | Complete | 2026-09-18 |

**Coverage:** 19/19 requirements mapped. Unmapped: 0.

---
*Roadmap created: 2026-09-17 for milestone v2.0 Harness Excellence*
