# ADR 0026 — Task-driven turn-lock (work-stack orchestration)

**Status**: Proposed
**Date**: 2026-06-15
**Supersedes**: the *single active flow* assumption of [`0013-togglable-deterministic-turn-lock.md`](0013-togglable-deterministic-turn-lock.md) (one `active_skill_doc` per turn, resolved from the live session / most-recent task). The deterministic turn-lock *mechanism* of 0013 (surface restriction inside the loop) stands; this ADR makes the *which task owns the lock* decision a stack, and adds deterministic resume.
**Builds on**: [`0022-proactive-task-monitor.md`](0022-proactive-task-monitor.md) (reuses its `requires_tasks` + eligibility + next-runnable machinery), [`0019-orchestrator-resumable-plan.md`](0019-orchestrator-resumable-plan.md).

---

## 1. Context

The orchestrator holds **one** lock slot. `resolve_active_task_lock_skill`
(`skill_tasks.py:295`) returns a single `SkillDoc`, and `active_skill_doc` is
singular at ~8 loop sites. Crucially, **completing a locked skill does not pick a
next task** (`engine.py:1431` closes the task, clears the session, returns to free
chat). There is no representation of *"work A is waiting on work B; finish B, then
resume A."*

Real flows need exactly that. The motivating case is account-gated services: a
`pre_alert`/`quotation` request requires a verified account session; if absent, the
user must detour through `identity_verification` (which may itself detour into
`onboarding`), and then the **original request must resume where it left off**.

Because the stack isn't modeled, it has been simulated with side channels, each a
workaround for the missing primitive:

- `pending_service_intent` — a conversation-context key reconstructing "what to resume".
- `next_tool` chains — forcing the model to re-call `use_skill` after the detour.
- `lock-companions: [use_skill]` — re-admitting `use_skill` under the lock (it is
  stripped by `restrict_tools_to_task_lock_skill`).
- utterance re-injection — so the resumed skill sees the original request, not the OTP code.
- retain-key gymnastics — so state survives the detour's session teardown.

Even assembled, resume stays **model-mediated** (the model must *choose* to
re-enter), so it is unreliable — observed live as the model acknowledging
("I'll now help you with your request") and stopping instead of continuing.

The fix is structural: **make the task store a work stack, and make resume the
orchestrator selecting the next runnable task.**

The deep-dive (this branch) confirmed the foundations already exist:

- `Task` has `status` + a free-form `data` bag, persisted to `conversation.tasks`
  (`memory/task_store.py`).
- `ProactiveTaskSpec.requires_tasks` + `are_prerequisites_met` +
  `pick_next_proactive_task` + priority/FIFO ordering already implement
  dependency + eligibility + next-runnable — but **only for `PROACTIVE` tasks**
  (`memory/task_proactive.py`, `task_eligibility.py`).
- `InterviewSession` has `to_dict`/`from_dict`, and the task-lock hooks
  (`needs_task_lock_rebootstrap`, `task_lock_runtime_ready`,
  `prepare_task_lock_turn`) exist — but interview state lives **only** in
  `conversation.context["interview"]`; the task carries no field snapshot.

We generalize what exists rather than invent.

## 2. Decision

Model in-conversation work as a **task graph** in the existing TaskStore, resolved
each turn to a single active task (the deterministic turn-lock of 0013 is unchanged
— still "restrict the surface to the active task"). Prerequisites **push**;
completion **pops and re-resolves**; resume is the orchestrator's selection, not the
model's. Task state is **durable** (snapshot on the task); a task-lock runtime (e.g.
the interview session) is **ephemeral**, rehydrated on activation.

### 2.0 Framework contract — domain-agnostic by construction

This is a **reusable jvagent service**, not a zoon feature. The orchestrator and
TaskStore gain a general work-stack capability that governs **any** task-lock skill
or action, present or future. **No domain vocabulary** (`account`, `OTP`, `zoon`,
`pre_alert`, …) appears anywhere in `jvagent/`. A consumer participates only through
generic seams:

1. **Precondition registry** — `register_precondition(name, predicate)` where
   `predicate` is `async (visitor) -> bool`. The harness knows preconditions only by
   opaque name; the agent/app binds names to checks. (zoon registers
   `account_session`; another app registers `entitlement`, `kyc`, `payment_method`,
   anything.)
2. **Declarative `requires-tasks`** — frontmatter on *any* skill (interview or not),
   parsed generically into the `SkillDoc`. `{when: <precondition name>, push:
   <skill name>, seed_from: [...]}`. The orchestrator pushes prerequisites with no
   knowledge of what they mean.
3. **Task-lock runtime hooks** — the existing hook family on a bound action
   (`resolve_task_lock_skill`, `task_lock_runtime_ready`, `prepare_task_lock_turn`)
   gains `snapshot_task_state(visitor) -> dict` and
   `rehydrate_from_task(visitor, snapshot)`. The orchestrator calls them generically;
   `InterviewAction` is **one** implementer. Any future task-lock action (a form
   filler, a wizard, a sub-agent) implements the same protocol and gets push/pop/
   resume + durable state for free.
4. **Task-runner dispatch** — a `Task.type` (`skill | action | plan | …`) selects a
   registered runner. Skills are the first runner; the graph machinery is type-agnostic
   so plans and sub-agent delegation reuse it unchanged.
5. **Seed/snapshot payloads** — generic `data["seed"]` / `data["snapshot"]` bags. The
   harness moves them; it never inspects their contents.

Everything in §2.1–2.4 below is core (generic). The zoon account-gate is *one
consumer*, isolated in §7 — it adds **zero** lines to `jvagent/` core beyond
registering one precondition and writing frontmatter.

### 2.1 Task graph (Layer 1)

Add to `Task`:

- `resumes: Optional[str]` — the task that becomes runnable when this one finishes
  (the back-link `requires_tasks` lacks today). `blocked_on` is derived from the
  inverse, or stored explicitly as `blocked_on: List[str]`.
- `seed: Dict` (or reuse `data["seed"]`) — inputs to (re)start the task: the
  originating utterance and any captured fields.
- `snapshot: Dict` (or `data["snapshot"]`) — durable interview state
  (collected/skipped fields, status).

Lift `are_prerequisites_met` and the next-runnable picker out of the
proactive-only path so they apply to **turn-lock** tasks. A task is *runnable*
when it is `pending|active` and all `blocked_on` are `completed`.

### 2.2 Stack resolver + pop-and-resume (Layer 2)

- `resolve_active_task_lock_skill` → **resolve the top runnable task** (unblocked,
  highest priority, then recency) and return its skill. The InterviewAction
  session-resolve path remains a fast hint, but the **task graph is authoritative**.
- On **any** task completion (`engine.py` completion, and generic task close), the
  orchestrator **re-resolves**. The just-unblocked parent (`resumes`) becomes the
  top runnable task → it is re-activated and re-grounded deterministically — **no
  `use_skill`, no `next_tool`, no companion glue.**
- Delete the single-slot assumptions enumerated in the deep-dive; `active_skill_doc`
  becomes "the resolved top of stack this turn."

### 2.3 Snapshot / rehydrate (Layer 3)

Generic via the task-lock runtime hooks (§2.0.3) — the orchestrator never reaches
into any runtime's internals:

- When a task-lock action's runtime mutates, the orchestrator persists
  `action.snapshot_task_state(visitor)` into the active task's `snapshot` (called
  where the runtime already persists). For `InterviewAction` this is the collected/
  skipped fields + status; another action snapshots whatever it owns.
- On activation, the orchestrator calls `action.rehydrate_from_task(visitor,
  snapshot)` when the live runtime is absent. The runtime is then free to be torn
  down during a detour; **the task is the source of truth, rebuilt on activation**,
  for any task-lock action. All retain-key handling for resume is deleted.

`InterviewAction` already has `to_dict`/`from_dict` and the rebootstrap hook, so it
is the reference implementer; the contract is what's new, not interview-specific.

### 2.4 Declarative prerequisites (generic)

*Any* skill declares preconditions in frontmatter; the harness enforces them
generically with no knowledge of their meaning:

```yaml
name: <gated_skill>
requires-tasks:
  - when: <precondition_name>      # resolved via the precondition registry (§2.0)
    push: <prerequisite_skill>     # any skill
    seed_from: [utterance]         # generic payload to carry into the resumed task
```

At activation, if `precondition_name` resolves to `False`, the orchestrator
**pushes** the named prerequisite task (`resumes = this task`, `blocked_on +=
[prereq]`) and surfaces **the prerequisite**, not the gated skill. The gated skill
never goes active — and never surfaces its tools — until its prerequisites are
`completed`. The harness only ever evaluates an opaque predicate and moves opaque
seed data; the *binding* of a precondition name to a check, and the *meaning* of the
seed, are entirely app-supplied (§2.0).

This single mechanism subsumes every bespoke gate (activation guard, deterministic
service gate, capability gate, resume rail) for *all* consumers — gating is just
"a skill with an unmet precondition."

## 3. Mechanism (turn loop)

```
each turn:
  active = resolve_top_runnable(task_graph)        # §2.2; None ⇒ free chat
  if active and active has unmet precondition:
      push prerequisite task (blocked_on/resumes wired); active = prerequisite
  rehydrate(active) from its snapshot              # §2.3
  surface(active)                                  # 0013 restriction, top-of-stack
  model advances active
  on completion(active):
      active.status = completed
      # next turn (or same-turn loop continuation) re-resolves → parent unblocked → resumes
```

Same-turn vs next-turn resume is no longer a correctness question — it is whether
the loop re-resolves within the turn after a completion. Because resolution is
deterministic and tool-surface-driven (not a model `use_skill`), re-resolving
in-loop after a completion yields reliable same-turn resume.

## 4. Invariants

1. **One active task owns the turn** (0013 unchanged) — but it is *the top runnable
   task in the graph*, not a single stored slot.
2. **A task cannot go active with unmet prerequisites** — its tools never surface
   early (kills the "stray get_status/set_fields before the gate" class).
3. **Resume is orchestrator-selected, never model-selected** — completion → re-resolve
   → parent. No `use_skill`/`next_tool`/companion dependency for resume.
4. **The task is the durable unit of state**; the interview session is ephemeral
   runtime rehydrated from the task snapshot. Teardown during a detour is safe.
5. **Preconditions are declarative**; the harness stays domain-agnostic (app binds
   precondition names to checks).
6. **No domain vocabulary in core.** `jvagent/` contains no app/domain term
   (`account`, `OTP`, `zoon`, `pre_alert`, …). Every consumer plugs in only via the
   §2.0 seams: registered preconditions, frontmatter, and the task-lock runtime hooks.
   A grep of `jvagent/` for any consumer's domain terms must return nothing. This is
   enforced as a CI guard, not a convention.

## 5. Migration (suite green per step)

1. **Graph fields + generalized picker.** Add `resumes`/`blocked_on`/`snapshot`;
   generalize `are_prerequisites_met` + next-runnable to turn-lock tasks. No behavior
   change yet (degenerate single-task case identical).
2. **Stack resolver + pop-and-resume.** Swap the single-pick resolver; re-resolve on
   completion. Existing single-skill flows unchanged; chained flows now resume.
3. **Snapshot/rehydrate.** Mirror fields to the task; rehydrate on activate; retire
   retain-key handling.
4. **Declarative `requires-tasks`.** Parse it; push prerequisites at activation; bind
   `account_session` in zoon. Port gating to it.
5. **Delete the duct tape (consumer side).** In zoon, remove `pending_service_intent`,
   the resume rail, `service_session_gate`/capability gate, `next_tool` resume
   forwarding, `lock-companions: [use_skill]`, utterance re-injection. Zoon gating
   collapses to frontmatter + one registered precondition. **Land the CI guard**
   (invariant 6): grep `jvagent/` for consumer domain terms → must be empty.
6. **Generalize.** The same push/pop/resume powers multi-step plans and sub-agent
   delegation, not just gating — validated with a second, non-zoon example skill in
   `jvagent`'s own examples app so the framework is exercised without a tenant.

## 6. Consequences

**Positive**
- Resume is deterministic and reliable; the entire gating/resume saga's failure
  modes (model wraps up, use_skill stripped, pending timing, retain wipes) are
  *structurally impossible*.
- Net deletion: the app gating layer becomes declarative config + one predicate.
- A general capability (work stacks) replaces a single-purpose hack; plans and
  delegation fall out of the same primitive.

**Costs / risks**
- Core change to the orchestrator's lock resolution and the interview session
  lifecycle — the highest-blast-radius area. Mitigated by phased migration with the
  suite green at each step and the degenerate single-task case preserved.
- Snapshot/rehydrate must capture enough state (collected + skipped + status; and a
  decision on session `context` scratch — default: rebuildable, not snapshotted).
- Two write paths during transition (session + task snapshot) until session-as-cache
  is fully retired.

**Open questions**
- Stack vs priority ordering when multiple independent flows coexist (default:
  prerequisites are LIFO; sibling plans are FIFO by `order`).
- Whether `requires-tasks.when` predicates live in frontmatter as names only (chosen)
  vs inline expressions (rejected — keeps the harness domain-agnostic).

## 7. Example consumer — account gating (zoon), for illustration only

zoon is the *first tenant*, not the design target. Everything zoon-specific lives in
the **app**, plugged into the §2.0 seams. The complete integration:

```python
# zoon app bootstrap — bind one precondition name to a check
register_precondition("account_session", lambda visitor: has_complete_account_context(visitor=visitor))
```

```yaml
# pre_alert_interview / quotation_interview SKILL.md frontmatter
requires-tasks:
  - when: account_session
    push: identity_verification_interview
    seed_from: [utterance]
```

That is the entire account gate. The detour chain is itself declarative —
`identity_verification_interview` declares its own `requires-tasks` (push
`onboarding_interview` when no account is found), so verify→onboard→resume is the
graph unwinding, with no app code in the loop.

What this **deletes** from zoon: `service_session_gate`, the capability gate,
`resume_service_action`, `pending_service_intent`, `guard_account_on_activate`
wiring, `lock-companions`, utterance re-injection, and the OTP-completion `next_tool`
forwarding. The account-session *checks* (`has_complete_account_context`, web expiry,
staleness) remain as app predicates — pure domain logic with no orchestration glue.

A second hypothetical tenant (e.g. a billing agent) reuses the identical machinery by
registering `payment_method` and writing `requires-tasks: [{when: payment_method,
push: add_card_interview}]` — zero shared code with zoon, zero new core.
