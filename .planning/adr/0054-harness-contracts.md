# ADR 0054 — Host-neutral harness contracts

**Status**: Accepted
**Date**: 2026-09-17
**Relation**: Extends [ADR-0012](0012-skill-executive-architecture.md) (thin orchestrator), [ADR-0014](0014-identity-on-agent-replyaction-egress.md) (identity/egress), [ADR-0018](0018-lean-tool-surfacing.md) (lean discovery), [ADR-0033](0033-identity-and-locking-substrate.md) (native identity tuples). Does not supersede them. Product roadmap: [`docs/HARNESS_EXCELLENCE_PLAN.md`](../../docs/HARNESS_EXCELLENCE_PLAN.md).

---

## 1. Context

jvagent already isolates users with `(memory_id, user_id)` and conversations with `session_id`. Hosts (embedded products) still risk leaking domain fields into core, serving process-global tool/skill caches across callers, and treating in-memory buses as durable delivery.

v2.0 Harness Excellence needs a frozen, host-neutral contract **before** persistence, outbox, or host adapters land. This ADR is that freeze. Runtime wiring is later packages (HP-02 … HP-08).

## 2. Decision

### 2.1 NativeCaller

The only public admission identity is:

```text
(agent_id, user_id, session_id)
```

Implemented as `NativeCaller` in [`jvagent/harness/contracts.py`](../../jvagent/harness/contracts.py). Host scopes (workspaces, organizations, Apps, domain schemas) map to `session_id` **outside** jvagent. Public models and APIs MUST reject `workspace_id`, `organization`, `organization_id`, `org_id`, and `content_profile_id`.

### 2.2 TurnRun states

```text
accepted → running → waiting_tool → waiting_approval → running
         → completed | failed | cancelled | recovery_required
```

Illegal transitions raise `HarnessContractError`. Terminal states do not resume silently. `recovery_required` is explicit; it is not auto-replay. Persistence is HP-04.

### 2.3 ToolSurfaceSnapshot

At turn admission the Orchestrator will receive one immutable snapshot (`snapshot_id` + NativeCaller + native/host descriptors + expiry). Cache keys MUST include `snapshot_id`. A revoked or expired snapshot is unusable for new dispatch. In-flight turns keep the admitted snapshot unless the host explicitly revokes it (HP-03).

Lean discovery (`find_tool`, `load_tool`, `find_skill`, `use_skill`) is unchanged.

### 2.4 Invocation and events

Every mutating dispatch will allocate `invocation_id` before the call (HP-05). Idempotency class is one of `idempotent` | `compensatable` | `non_retryable`. Exactly-once for third-party effects without an idempotency mechanism is out of scope.

Outbound frames will use `EventEnvelope` (`session_id`, monotonic `sequence` ≥ 1, `cursor`, `message_id`, `correlation_id`, `snapshot_id`) persisted before fan-out (HP-06). Delivery is at-least-once; single-egress stays at ReplyAction / EgressGate.

### 2.5 HostCapabilityProvider

Optional protocol (async, matching jvagent I/O):

```text
resolve_snapshot(caller) -> ToolSurfaceSnapshot
invoke(snapshot_id, invocation_id, tool_name, payload) -> ToolResult
load_skill(snapshot_id, skill_key) -> SkillMaterialization
invalidate(selector) -> None
```

Authority is bound server-side. Model-generated payloads MUST NOT carry `authority`, `trust_tier`, `capability_token`, `snapshot_secret`, or `isolation_backend`.

Native, embedded, and remote transports share `tests/conformance/` (HP-08). The sample host is `tests/conformance/fixtures/fake_host/` — not another product's model.

### 2.6 Guarantee split

Single-process mode remains supported with narrower guarantees (process-local bus/caches). Active-active and crash-safe claims require HP-06 and HP-07 evidence. Subprocess resource limits are development-only containment, not a sandbox (HP-09).

### 2.7 Thin harness

This ADR adds reliability mechanics only. The Orchestrator does not gain semantic routing, intent classification, or host-domain workflows. See [`docs/thin-harness.md`](../../docs/thin-harness.md).

## 3. Consequences

- Contract tests run today; runtime isolation/outbox/provider tests are skipped until their HP.
- `register_host_skill_provider` remains until HP-08 replaces it; it is process-global and must not grow host-domain fields.
- ADR-0019 soft plan resume is not a TurnRun journal.

## 4. Verification

`tests/harness/test_contracts.py` and `tests/conformance/` (marker `harness_conformance`).
`tests/action/orchestrator/test_no_interview_coupling.py` still passes — harness types do not import interview.
