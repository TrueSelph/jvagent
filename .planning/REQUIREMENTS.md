# Requirements: jvagent v2.0 Harness Excellence

**Defined:** 2026-09-17
**Core Value:** A dependable, graph-native harness for many agents, users, and sessions — model as pilot, tools as controls, skills as flight plan.
**Source:** [`docs/HARNESS_EXCELLENCE_PLAN.md`](../docs/HARNESS_EXCELLENCE_PLAN.md)
**Conformance:** HC-01 … HC-12 in that plan map 1:1 onto the IDs below.

## v2.0 Requirements

### Contracts and baseline

- [ ] **CTRT-01**: A native, embedded, or remote integrator can run the same harness conformance suite against frozen TurnRun, snapshot, invocation, event, and provider fixtures; rejected transitions are explicit; no host-domain field appears in public jvagent models or APIs (HC-01 contract half)
- [ ] **BASE-01**: An operator can name every process-local component, its scope, its replacement decision, and the regression test that proves restart or multi-worker loss

### Identity and snapshots

- [ ] **IDNT-01**: Concurrent callers on distinct agents, users, and sessions stay isolated using only `(agent_id, user_id, session_id)` — no host-specific fields in jvagent core (HC-01)
- [ ] **IDNT-02**: Concurrent session admission across workers produces one User and one Conversation for the same identity; same-session ownership policy is explicit and tested
- [ ] **SNAP-01**: A tool or skill snapshot cannot leak across sessions or be reused after expiry or revocation (HC-02)
- [ ] **SNAP-02**: Dynamic tool and skill changes take effect on the next snapshot without contaminating any other in-flight caller

### Durable execution

- [ ] **RUN-01**: A crash before, during, or after tool dispatch has an explicit recovery result and never silently duplicates a supported side effect (HC-03)
- [ ] **RUN-02**: Model outage, retry, fallback, budget exhaustion, cancellation, and tool timeout leave an inspectable terminal run state (HC-09)
- [ ] **INV-01**: Retries reuse the same `invocation_id`; mutating native tools declare idempotency class; non-retryable tools produce a typed recovery state
- [ ] **DELV-01**: SSE and channel delivery replay events in order using cursors and render each final response once (HC-04)

### Distributed runtime and extensibility

- [ ] **DIST-01**: Two workers can serve different sessions concurrently and coordinate same-session ownership correctly (HC-05)
- [ ] **DIST-02**: Worker loss preserves queued delivery and either resumes or safely marks active runs for recovery (HC-06)
- [ ] **HOST-01**: Native, embedded-host, and remote-host tool providers pass the same invocation and revocation contract suite (HC-08)
- [ ] **HOST-02**: A sample host can supply per-session dynamic tools and skills; revocation takes effect at the next snapshot; jvagent remains unaware of the host's data model
- [ ] **SKIL-01**: JV and Claude skill bundles materialize from verified manifests into isolated caller slices (HC-07)
- [ ] **SKIL-02**: Skill activation is reproducible from its digest; a revoked or changed skill cannot run under a stale snapshot; untrusted script skills are refused without an approved isolation backend

### Operational excellence

- [ ] **OBSV-01**: An operator can explain any completed or failed turn from one correlation id without accessing another user's data (HC-10)
- [ ] **PERF-01**: Load tests preserve p95 targets and event ordering under many users and sessions; no optimization weakens ordering, identity isolation, or egress (HC-11)
- [ ] **REL-01**: A release record identifies artifact digest, contract versions, supported topology, evidence, limitations, and rollback path (HC-12)

## Future (not this milestone)

- Which durable transport is first for event outbox and distributed coordination
- Separate checkpoint vs event retention policies per backend
- Whether background work uses the same TurnRun executor or a sibling durable worker
- External skill publisher registry and revocation service (after signed manifests and isolation backends prove out)

## Out of Scope

| Feature | Reason |
|---------|--------|
| Host concepts (workspaces, organizations, Apps, domain schemas) in jvagent core | Hosts map scopes to session ids; jvagent stays host-neutral |
| Semantic router / workflow designer / business-rule engine in the Orchestrator | Thin harness: judgment stays in skills and the model |
| A second memory database competing with jvspatial graph state | Graph remains the runtime state substrate |
| Exactly-once for third-party side effects with no idempotency mechanism | Harness supplies invocation identity; domain tools own exactly-once |
| Treating subprocess resource limits as a sandbox for untrusted code | Dev-only containment; untrusted scripts need an approved isolation backend |
| Requiring active-active for every deployment | Single-process remains supported with explicitly narrower guarantees |
| Embedding Integral or any other product's model in tests | HP-08 uses a small independent host fixture; `examples/jvagent_app` is the native reference |

## Traceability

| Requirement | Phase | HP | Status |
|-------------|-------|----|--------|
| CTRT-01 | Phase 1 | HP-00 | Pending |
| BASE-01 | Phase 1 | HP-01 | Pending |
| IDNT-01 | Phase 2 | HP-02 | Pending |
| IDNT-02 | Phase 2 | HP-02 | Pending |
| SNAP-01 | Phase 2 | HP-03 | Pending |
| SNAP-02 | Phase 2 | HP-03 | Pending |
| RUN-01 | Phase 3 | HP-04 | Pending |
| RUN-02 | Phase 3 | HP-04 | Pending |
| INV-01 | Phase 3 | HP-05 | Pending |
| DELV-01 | Phase 3 | HP-06 | Pending |
| DIST-01 | Phase 4 | HP-07 | Pending |
| DIST-02 | Phase 4 | HP-07 | Pending |
| HOST-01 | Phase 4 | HP-08 | Pending |
| HOST-02 | Phase 4 | HP-08 | Pending |
| SKIL-01 | Phase 4 | HP-09 | Pending |
| SKIL-02 | Phase 4 | HP-09 | Pending |
| OBSV-01 | Phase 5 | HP-10 | Pending |
| PERF-01 | Phase 5 | HP-11 | Pending |
| REL-01 | Phase 5 | HP-12 | Pending |

**Coverage:**
- v2.0 requirements: 19 total
- Mapped to phases: 19
- Unmapped: 0 ✓

---
*Requirements defined: 2026-09-17*
*Last updated: 2026-09-17 after milestone v2.0 roadmap*
