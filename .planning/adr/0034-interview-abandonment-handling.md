# ADR-0034 — Interview Abandonment: Field Unavailability, Parking, and Staleness Reaping

- **Status:** Accepted — L1–L4 + L6 implemented 2026-07-20; L5 (two-strike
  soft-abandon) deferred (see Implementation status). Spec approved 2026-07-19.
- **Date:** 2026-07-19
- **Related:** ADR-0026 (task-driven turn-lock, snapshot/rehydrate), ADR-0031 (skill SOP extends), QUO-2 reaper pattern (consumer precedent: TaskMonitor-driven timeout sweep for async jobs).

## Context

An active interview assumes forward progress: every required field will
eventually be supplied and the flow ends in `complete` or an explicit
`reset`/`cancel`. Reality on a conversational channel breaks that assumption
in three distinct ways, none of which the framework currently handles:

1. **The user cannot supply a required field.** "I don't have the tracking
   number" leaves the interview wedged: the field is required, the validator
   rejects filler, and the model can only re-ask. The turn-lock holds the
   conversation on a question the user has already said they cannot answer.

2. **The user soft-abandons mid-interview.** They pivot to a different
   service and never return. Lock-companions absorb *side questions*, but a
   hard topic change leaves the interview task active and the turn-lock
   steering every subsequent turn back to a flow the user has left.

3. **The user silently disappears.** The session and its SKILL task linger
   `active` indefinitely. The next contact — possibly days later — lands
   inside a stale interview instead of fresh routing.

All three end the same way today: a hung interview that only an explicit
user cancellation clears. Graceful inference of abandonment must be a
**standard interview behaviour**, not per-tenant hook code.

## Decision

Three layers, all implemented in jvagent, all declaratively configured from
SKILL.md frontmatter. Consumers (e.g. zoon-ai) declare policy; they write no
abandonment code.

### 1. Field-level unavailability (`on_unavailable`)

New optional per-field frontmatter key:

```yaml
fields:
  - key: tracking_number
    required: true
    on_unavailable: park        # park | cancel | relax   (default: park)
    # relax additionally requires:
    relaxable: true             # forbidden on compulsory fields — see below
```

New interview tool **`interview__field_unavailable(key, reason?)`** — the
model calls it when the user states they cannot supply the currently pending
field ("don't have it", "can't find it", "I'll have to check"). Detection is
model-driven (it's a language judgement); the *consequence* is deterministic
and server-owned:

- **`park`** (default for required fields): snapshot the session onto the
  SKILL task (existing ADR-0026 `snapshot_task_state`), set task status
  `parked`, clear the live session, release the turn-lock. Server-composed
  reply: what was saved, what is still needed, and that the flow resumes the
  moment they return with it. Parked tasks are invisible to the task-lock
  resolver (they do not own turns) but are found by the **rehydrate trigger**:
  on a later utterance that routes to the same skill, `use_skill` activation
  finds the parked task, rehydrates the snapshot (fields intact), marks the
  task active again, and continues from the first missing field.
- **`cancel`**: close the task `cancelled` via the skill's existing
  reset/cancel handler; server-composed goodbye naming what to bring next
  time. For fields whose value is time-boxed anyway (OTP codes).
- **`relax`**: mark the field `skipped` and continue — permitted **only**
  when the field declares `relaxable: true`. **Compulsory-field rule:** a
  required field is compulsory by default; `on_unavailable: relax` without
  `relaxable: true` is a **spec-load validation error** (fail fast at
  `_validate_contract`, not at runtime). This makes "can this record exist
  without the field?" an explicit product decision in the frontmatter, never
  an inference. Optional fields need no policy (skip already covers them).

The completion handler remains the single integrity gate: `relax` never
bypasses `compute_missing_required` for fields not marked skipped, and a
parked session re-validates on rehydrate (spec may have changed between park
and resume; `InterviewSession.from_dict` version drift falls back to fresh).

### 2. Soft-abandon inference (two-strike orchestration rule)

Orchestrator-level, no per-skill config. While a task-lock interview is
active, if a turn (a) routes-matches a *different* skill with high confidence
(the utterance matches another skill's description, not a companion side
question), and (b) contains no extractable content for any pending field,
the orchestrator appends a server-prep strike note. On the **second
consecutive** such turn it asks once, server-composed: *"Want me to set aside
the <interview title> for now and help with that instead?"* — yes ⇒ apply the
skill's `on_abandon` policy (below) and route the new intent in the same
turn; continued engagement with the interview clears the strikes.

```yaml
interview:
  on_abandon: park              # park | cancel   (default: park)
```

### 3. Staleness reaper (silent abandonment)

Rides the existing TaskMonitor tick (same infrastructure as the QUO-2 job
reaper). Frontmatter TTLs, all optional — no declaration, no reaping:

```yaml
interview:
  nudge_after: 4h               # one proactive reminder (optional)
  abandon_after: 24h            # then apply on_abandon (park|cancel)
  parked_expire_after: 30d      # parked snapshots eventually cancel too
```

- Idle is measured from the task's last update (any field store, nudge, or
  rehydrate refreshes it).
- `nudge_after` sends **one** proactive message per task, ever ("Still want
  to finish your support ticket? Pick up where we left off any time — or
  ignore this and I'll set it aside.").
- `abandon_after` applies `on_abandon`. Parked tasks survive until
  `parked_expire_after`, then close `cancelled` (no message — the user is
  long gone; the next contact routes fresh).
- **Rails:** the reaper never touches (a) a task whose conversation holds an
  active turn (in-flight lock/lease), (b) a task blocked on a prerequisite
  (ADR-0026 gated parent — it is *waiting*, not idle; its prerequisite's own
  TTLs govern), or (c) non-interview task types.

### Task-state additions

`TaskStore` gains status `parked` (terminal for the resolver, non-terminal
for rehydrate/reaper). `pick_top_runnable` and the orphan sweep ignore it.
The snapshot lives on the task as today.

## Consequences

- A wedged "I don't have it" interview releases the conversation in one
  turn, keeps everything already collected, and resumes losslessly.
- No conversation is permanently owned by a dead interview; the reaper
  guarantees eventual cleanup without any explicit user cancel.
- The compulsory-field rule makes data-integrity relaxation impossible to
  enable by accident: it is a two-key declaration (`on_unavailable: relax` +
  `relaxable: true`) validated at load.
- New surface: one tool (`interview__field_unavailable`), one task status
  (`parked`), four frontmatter keys (`on_unavailable`, `relaxable`,
  `on_abandon`, TTL trio). All backwards-compatible defaults (undeclared =
  today's behaviour except the new default `park` on explicit unavailability,
  which strictly improves on wedging).

## Consumer implementation — zoon-ai policy matrix

| Skill | Field / scope | Policy | Rationale |
| --- | --- | --- | --- |
| ticket_interview | `tracking_number` | `on_unavailable: park` | User will return with the number; keep contents/arrival already given. |
| ticket_interview | `expected_arrival`, `package_contents` | `park` (default) | Same. |
| ticket_interview | interview | `on_abandon: park`, `nudge_after: 4h`, `abandon_after: 24h`, `parked_expire_after: 30d` | Support cases are worth a nudge. |
| pre_alert_interview | `tracking_numbers` | `park` | Pre-alert without a number is meaningless; they'll come back with it. |
| pre_alert_interview | per-item `description` / `invoice_value` | `park` | Partial batch survives parking (for_each state snapshots whole). |
| pre_alert_interview | interview | `on_abandon: park`, `nudge_after: 4h`, `abandon_after: 24h` | — |
| quotation_interview | `product_urls` | `cancel` | Nothing collected yet worth parking; a later link starts clean. |
| quotation_interview | interview | `on_abandon: cancel`, `abandon_after: 12h` | Quotes go stale; escalated jobs already have the QUO-2 reaper. |
| account_provisioning | `otp_code` | `cancel` | Codes expire; parking a half-verified identity is a security smell. |
| account_provisioning | signup fields (`id_number` … `gender`) | `park` | Don't make them re-type identity data. |
| account_provisioning | interview | `abandon_after: 30m` (no nudge) | Short-lived by nature; a gated parent it blocks stays parked per ADR-0026 rails and resumes on the next attempt. |

No zoon Python changes: policies are frontmatter-only. The only zoon-side
prompt work is one line in each skill's Tone/Rules reminding the model to
call `interview__field_unavailable` when the user says they can't provide
the pending field (mirrors the existing skip_field guidance).

## Implementation order

1. `spec.py` FieldDef/InterviewSpec keys + `_validate_contract` compulsory
   rule (relax ⇒ relaxable).
2. `interview__field_unavailable` tool + park/cancel/relax executors
   (reusing snapshot_task_state / clear / close_task).
3. `parked` status + resolver/orphan-sweep exemptions + rehydrate-on-activate
   trigger in `on_skill_activate`.
4. Reaper rule on TaskMonitor (+ nudge egress via proactive send).
5. Two-strike orchestration rule.
6. zoon frontmatter matrix + guidance lines; live verification battery
   (park→resume, relax-forbidden load error, reaper TTL with shortened
   clock, two-strike switch).

## Implementation status (2026-07-20)

Layers 1–4 and 6 are implemented, tested, and committed. Layer 5 (two-strike
soft-abandon) is deferred — it modifies the main orchestrator loop's companion
gate (the hottest path in the system) and carries two design points the spec
leaves implicit; it should land as its own focused, reviewed change.

- **L1 spec + validation** — `spec.py`: `FieldDef.on_unavailable`/`relaxable`,
  `InterviewSpec.on_abandon` + TTL trio, compulsory-field rule (relax ⇒
  relaxable) enforced at parse, `parse_duration_seconds`. (jvagent `4da65694`)
- **L2 `interview__field_unavailable`** — tool + park/cancel/relax executors in
  `engine.handle_field_unavailable`; base SKILL.md allowed-tools + intent table.
  (jvagent `4da65694`)
- **L3 parked status + resume** — `task_store` `parked` status (+ `park()` /
  `resume_parked()`); `handle_start` rehydrates a parked task on re-activation.
  `pick_top_runnable`/orphan-sweep already ignore it (not runnable). (jvagent
  `4da65694`)
- **L4 staleness reaper** — `interview/reaper.py` on the TaskMonitor tick:
  nudge / abandon(park|cancel) / expire, with blocked-on + non-interview rails.
  (jvagent `908bb52a`)
- **L6 zoon policy matrix** — frontmatter-only across ticket / pre_alert /
  quotation / account_provisioning + guidance lines. (zoon `00bf880`)

### L5 design (deferred — for the follow-up)

Primary seam: the companion gate at `loop.py:588-610` — a non-companion
`use_skill(name=…)` while task-locked is currently hard-blocked and steered
back. That block already is strike condition (a); since the model emits one
action per turn, choosing `use_skill` over `set_fields` inherently satisfies
condition (b). There is no routing confidence score (routing is model-driven
`use_skill`), so "high-confidence different-skill match" == the model named a
non-companion skill.

Deterministic, NLU-free plan:
- Strike counter on `conversation.context` keyed by locked skill name (mirror
  `continuation.py note_locked_flow_error` / `_ERROR_STREAK_KEY`, limit 2). Store
  the collected-field count alongside it.
- On a gate hit: read `len(session.get_collected_summary())`. If it grew since
  the last strike, the user engaged the interview between attempts → reset the
  streak to 1 (this is the "continued engagement clears the strikes" rule,
  observed deterministically rather than via NLU). Otherwise increment.
- Streak 1: bounce as today. Streak 2: bounce but compose the one-turn ask
  ("Want me to set aside the <spec.title> for now and help with that
  instead?"). Streak ≥ 3 (the model persisted past the ask == the "yes"): apply
  `spec.on_abandon` by reusing the `reaper._apply_abandon` / `handle_field_
  unavailable` park-or-cancel shape, then clear `active_skill_doc` and
  `continue` so this turn's utterance re-routes through the now-unlocked surface.
- Persistence-as-confirmation is chosen over an affirmative-detection heuristic
  to keep the hot path deterministic. The ADR's literal "ask on 2nd, yes on the
  next turn" is honored in spirit; a persisted repeat is the yes.

Open verification for the follow-up: the two-strike switch (live), and that a
reset fires when the user answers a field between two off-topic attempts.
