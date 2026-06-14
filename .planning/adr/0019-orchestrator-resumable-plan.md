# ADR 0019 — Orchestrator-owned resumable plan (`update_plan`)

**Status**: Accepted
**Date**: 2026-05-31
**Relation**: Extends [ADR-0012](0012-skill-executive-architecture.md) (the orchestrator loop + unified tool surface) and [ADR-0013](0013-togglable-deterministic-turn-lock.md) (flow continuation via the conversation `TaskStore`). Activates the `AGENTIC_LOOP` task type that ADR-0013's continuation reserved but never wrote.

---

## 1. Context

The orchestrator runs a bounded think-act-observe loop within a single turn
(`activation_budget`, default 24). Intermediate tool results live in an
in-memory `observations` list that evaporates when the turn ends. The only
cross-turn state it reads is an *active flow* pointer (`active_flow_owner`) that
**InteractAction flows** (e.g. the interview) record on the `TaskStore` for
themselves; the orchestrator never writes a task for its own multi-step work.

For the dominant case — a multi-step request that completes within one turn —
that is fine: the work finishes and conversation memory carries the result. But
three cases have no checkpoint and so cannot resume:

1. **Crash / restart mid-turn** — all observations are lost; the next turn cold-starts and may redo already-executed side effects.
2. **Budget/duration cutoff on a real multi-step task** — the partial-compose replies with "what I have," but discards the in-flight plan; the user's "continue" forces a full re-plan from prose history.
3. **Genuinely long / multi-turn agentic work** — there is no first-class way for the orchestrator to own a task that legitimately spans turns (as opposed to handing off to an IA flow). Plus the lost observability: no externalized checklist to view.

The orchestrator is deliberately a **lean, performant harness**. Persisting a
plan + step updates costs DB writes (`conversation.save()`), so the mechanism
must not tax turns that don't need it.

## 2. Decision

Add an **opt-in, model-driven** plan the orchestrator owns, reusing the existing
`TaskStore` (`Task`/`Step`) and the reserved `AGENTIC_LOOP` task type. No new
storage, no new infra — wiring.

### 2.1 Gate

A `planning: bool` attribute on `OrchestratorInteractAction`, **default `False`**.
When off: the tool is not assembled, the prompt hint is not emitted, no task is
written — behavior is byte-for-byte unchanged. When on: cost is incurred only
when the *model* calls the tool (its choice, for genuinely multi-step work).
Pay-for-what-you-use.

### 2.2 `update_plan` tool (model-driven)

A core tool, surfaced only when `planning`, bound to the turn's walker. The
model re-sends its whole checklist each call (TodoWrite-style, idempotent
full-state overwrite). Steps carry a loose status (`pending`/`in_progress`/
`done`/`skipped`), normalized via `normalize_step_status`. The handler creates a
single active `AGENTIC_LOOP` task (`owner_action` = the orchestrator class) on
first call and reconciles it thereafter — there is never more than one active
orchestrator plan per conversation, so plans can't accumulate.

### 2.3 Soft resume

At turn start, if `planning` and an active orchestrator plan with pending steps
exists, `plan_resume_note` injects the persisted checklist plus an instruction
to continue from the first unfinished step and not redo completed ones. This is
**soft**, mirroring `active_flow_note` (not a hard lock, consistent with
`lock_active_flow=False`): if the user changed topic, the model handles that and
the plan stays parked. `AGENTIC_LOOP` remains excluded from IA-flow routing
(there is no IA tool to call); resume is the orchestrator re-reading its own
plan, not delegating.

### 2.4 Lifecycle (`_finalize_plan`, in the loop's `finally`)

On every loop exit: if the plan's steps are all terminal → `complete()` + delete
(clean). If steps remain pending — a natural end with parked work, **or** a
budget/duration/crash cutoff — leave it **active** so the next turn re-surfaces
it. Budget/"continue" resume thus falls out for free, with no separate
checkpoint mechanism. Because each `update_plan` call persists, step statuses up
to a crash survive and resume picks up from there.

## 3. Consequences

- **Resumability** for interrupted multi-step turns (crash, budget, "continue") and a structured, viewable plan for observability — both without taxing simple turns.
- **One writer** for `AGENTIC_LOOP`: the orchestrator's `update_plan`. The reserved-but-dead task type now has a purpose.
- **Lean preserved**: default off; when on, the only cost is the model's own `update_plan` calls.

### Limitations (deferred)

- **Side-effect idempotency is out of scope.** Resume tells the model which steps are done; it does not make already-executed tool side effects (files written, messages sent) idempotent. A step that re-runs may repeat its side effect. Skills/tools that must be exactly-once remain responsible for their own guards.
- **Stale active plans**: a plan the model never finishes stays active until overwritten. Since `update_plan` overwrites the single plan, this can't accumulate, but a long-abandoned plan persists on the conversation until pruned. Automatic expiry can be added later if needed.

## 4. Alternatives considered

- **Automatic checkpointing only** (no tool): the loop persists observations on budget/crash exit on its own. Robust for crash resume, but adds cost to every multi-step turn, gives no externalized plan/observability, and removes model control. Rejected as the primary; the model-driven plan already yields budget-resume for free.
- **Default-on**: rejected — violates the lean-harness principle for agents that never do multi-step work.
- **Hard resume (re-lock into the plan)**: rejected — inconsistent with the `lock_active_flow=False` philosophy and would shove off-topic turns back into a parked plan.

## 5. Configuration

```yaml
- action: jvagent/orchestrator
  context:
    planning: true        # default false; surfaces update_plan + persists plans
    # planning_prompt: "..."   # optional override of the gated nudge
```

Implementation: `planning`/`planning_prompt` attributes + `_finalize_plan` in
`orchestrator_interact_action.py`; `build_plan_tool`/`update_plan` in
`core_tools.py`; `active_plan`/`plan_resume_note`/`PLAN_TASK_TYPE` in
`continuation.py`; `PLANNING_PROMPT` in `prompts.py`; `TaskHandle.sync_plan` +
`normalize_step_status` in `memory/task_store.py`. Covered by
`tests/action/orchestrator/test_plan_persistence.py`.
