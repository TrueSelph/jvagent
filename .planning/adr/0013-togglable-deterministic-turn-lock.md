# ADR 0013 — Togglable deterministic turn-lock

**Status**: Accepted
**Date**: 2026-05-29
**Supersedes**: invariants 2 and 3 of [`0012-skill-executive-architecture.md`](0012-skill-executive-architecture.md) (flow continuation is model-mediated / turn-lock is emergent). The rest of ADR-0012 stands.

---

## 1. Context

ADR-0012 made flow continuation **model-mediated**: when a control-task pointed
to an active flow (the signup interview), the orchestrator surfaced that flow's
tool and injected a note, then let the model decide each turn whether to continue
the flow or route elsewhere. Turn-lock was therefore *emergent* — the flow stayed
available and was nudged, but never imposed.

That behavior is desirable for interruptibility (an off-topic question mid-flow is
answered, and the flow resumes when the user returns), but it is **non-deterministic**:
whether an in-progress flow advances on a given turn depends on the model picking
its tool. For flows that must reliably own the conversation until they finish, the
maintainer wants a **mechanistic** guarantee: if a task points to an IA, the turn
goes to that IA — full stop.

## 2. Decision

Add a boolean config attribute `lock_active_flow` to `SkillExecutiveInteractAction`,
**default `True`**.

- **`lock_active_flow=True` (default) — deterministic turn-lock.** The lock is
  expressed as a **tool-surface restriction inside the loop**, not a side path.
  After assembling the tool surface, `_run_loop` checks
  `continuation.active_flow_owner(visitor)`; if an owner resolves to an IA that
  furnished a tool, the loop **restricts the callable surface to that one tool**
  and dispatches it immediately (no model round-trip). The IA's tool is
  visitor-bound, AC-gated on `tool:delegate:{name}`, and terminal — exactly the
  same `wrap_action_tool` binding used for model-mediated routing — so the lock
  reuses the unified tool surface rather than a bespoke dispatch. The IA owns
  every turn until it clears its own control-task; it receives all input,
  including off-topic messages, and interruption/cancellation are the IA's own
  concern (it already carries cancel/skip/update continuation intents).

- **`lock_active_flow=False` — model-mediated continuation.** The ADR-0012
  behavior: the active flow's tool is surfaced into the prompt with a guidance
  note, and the model chooses each turn whether to continue it or route an
  off-topic request elsewhere. Interruptibility is automatic.

Continuation is the only behavior that changes; everything else in ADR-0012
(one tool surface, routing-is-tool-selection, walk-path curation, AC gating,
model-discretionary egress) is unchanged. The deterministic path reuses the same
unified binder (`wrap_action_tool`) — no separate dispatch machinery.

## 3. Invariants (replacing ADR-0012 §3 invariants 2–3)

2. **Flow continuation mode is configurable** via `lock_active_flow`. Active-flow
   detection (`active_flow_owner`) is always a deterministic read of persisted
   `TaskStore` state (no model).
3. **Turn-lock is deterministic when `lock_active_flow=True`** (the loop's
   callable surface is restricted to the active flow's IA tool, which is
   dispatched with no model round-trip) and
   **emergent/model-mediated when `False`** (the flow's tool is surfaced and the
   model decides). In both modes the flow's control-task persists across turns
   and is cleared only by the flow's own session logic.

ADR-0012 invariants 1, 4, 5, 6, 7 are unaffected.

## 4. Consequences

**Gained**: a reliable, mechanistic turn-lock for flows that must own the
conversation, with no reliance on model tool-selection — while keeping the
interruptible model-mediated mode one config flag away.

**Cost**: with the default `True`, an off-topic message during an active flow is
handled by the IA rather than answered elsewhere; agents that want the
interruptible behavior set `lock_active_flow: false`. The IA, not the orchestrator,
owns interruption/cancel semantics in locked mode (consistent with the
no-IA-hacking constraint — the only orchestrator-facing surface remains
`get_tools()`).

**Implementation**: `SkillExecutiveInteractAction.lock_active_flow` + the
surface-restriction branch in `_run_loop`; exposed (commented) in the `executive`
scaffold profile and the reference agent. Tests:
`tests/action/skill_executive/test_flow_lock.py`.
