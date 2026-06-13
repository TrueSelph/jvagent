# Interview multi-response extraction + orchestrator decoupling — design

**Date:** 2026-06-13
**Status:** Proposed (awaiting review)
**Scope:** `jvagent/action/interview/` (payload contract + processing discipline), `jvagent/action/orchestrator/` (remove interview coupling)

---

## 1. Context

The interview action already stores multiple fields per call: `interview__set_fields`
accepts `{"fields": {...}}` and processes each field through `pre_processor → validator →
store → post_processor` ([engine.py:687-835](../../../jvagent/action/interview/engine.py)).
Two problems block a clean multi-response experience:

1. **Fat payloads.** Every tool return re-ships field metadata — `fields`,
   `field_definitions`, `awaiting_fields`, `guidance_page`, `field_keys`,
   `active_path_keys` ([engine.py:631-985](../../../jvagent/action/interview/engine.py)).
   The model re-reads the same prompts/guidance on every call.
2. **Orchestrator coupling.** Two hardcoded interview references violate the goal of a
   reusable, skill-agnostic agentic loop:
   - Compound-extraction nudge keyed on the literal `"interview__set_fields"`
     ([skill_tasks.py:507-512](../../../jvagent/action/orchestrator/skill_tasks.py)).
   - Prep visualization filtered on the `interview__` namespace prefix
     (`_emit_server_prep_tool_thoughts`,
     [orchestrator_interact_action.py:2405-2424](../../../jvagent/action/orchestrator/orchestrator_interact_action.py)).

Field metadata is already serializable via `field_def_to_dict`
([spec.py:120-139](../../../jvagent/action/interview/spec.py)), and interview activation
already flows through a generic, interview-owned hook
(`on_skill_activate` → `_handle_start`,
[engine.py:1592-1651](../../../jvagent/action/interview/engine.py)). This design leverages
both.

## 2. Goals / non-goals

**Goals**
- One user utterance → model submits all confident key/value pairs in a single
  `set_fields` call.
- Each submitted value retains full per-response discipline (`pre → validator → post`),
  processed in field-definition order with incremental branch settlement.
- Field metadata (key/prompt/guidance) delivered through the activation/prep channel, not
  repeated in `set_fields` tool returns.
- Orchestrator carries zero interview-specific knowledge.

**Non-goals**
- Splitting `engine.py` into smaller modules (deferred; out of scope this pass).
- Changing validator/hook authoring contracts (`custom_tools.py` signatures unchanged).
- Changing the turn-lock / TaskStore continuation mechanism.

## 3. Locked decisions

| # | Decision | Choice |
|---|---|---|
| 1 | Field-reference delivery | **Activation + on-demand pull.** `field_reference` shipped in the activation envelope; `interview__get_status` returns the full `field_reference` on demand. Never repeated in `set_fields`/`next_field`. Model-pull only — no per-turn server push, so the thin-harness "no prep observations" invariant holds. (Supersedes an earlier "re-emit every turn" choice, which would have required either a per-turn prep observation or orchestrator coupling.) |
| 2 | Multi-response semantics | **Order, store, settle, report** — process in field order, store valid, settle branches incrementally, ignore unreachable, isolate failures, return one composed directive. |
| 3 | Compound-extraction rule location | **Interview base `SKILL.md` SOP body** — removed from the orchestrator. The base SOP already carries this rule, so C1 is deleting a duplicate. |
| 4 | Scope | **Contract redesign + orchestrator decoupling only.** No `engine.py` restructure. |
| 5 | `next_field` return | **Key + prompt** now (safe); key-only is the lean target once SOP key→prompt mapping is validated. |

## 4. Contract redesign — three payloads

### 4.1 Activation / prep payload — the field reference struct

Produced by the interview action (`_handle_start` for turn-0 activation; the
`prepare_task_lock_turn` hook for every subsequent turn). The orchestrator relays it
verbatim — it never constructs or inspects field data.

```jsonc
{
  "field_reference": [
    {
      "key": "string",
      "prompt": "string",            // question to ask
      "guidance": "string",          // acceptance criteria
      "required": true,
      "validator": "string",         // name only, not the function
      "branches": [{"when": {...}, "goto": "key"}],   // when present
      "else": "key"                  // when present
    }
    // ... ALL fields, in definition order
  ],
  "start_field": "first_unanswered_key",
  "collected": ["already_answered_key"],   // keys only, no values repeated
  "usage_note": "Full field guidance is here. Later tool results return only directives and per-field outcomes — refer back to this reference."
}
```

- Built from `field_def_to_dict` ([spec.py:120-139](../../../jvagent/action/interview/spec.py)) — reuse, no new serializer.
- **On-demand recovery, not per-turn push.** `field_reference` ships once in the activation
  envelope. When context thins on a long interview, the model re-pulls it by calling
  `interview__get_status` (with `include_field_definitions`-equivalent behavior), which
  already returns the full ordered field list
  ([engine.py:1560-1565](../../../jvagent/action/interview/engine.py)). The base SOP already
  instructs this refresh ([SKILL.md:23](../../../jvagent/action/interview/SKILL.md)). No
  server-pushed per-turn observation ⇒ the thin-harness "no prep observations" invariant is
  preserved, and the orchestrator needs no interview knowledge.

### 4.2 `set_fields` slim return

**Dropped:** `fields` (full value map), `field_definitions`, `awaiting_fields`,
`guidance_page`, `guidance_next_offset`, `field_keys`, `required_keys`, `active_path_keys`,
`missing_required`.

**Retained — only what guides the next action:**

```jsonc
{
  "ok": true,
  "status": "ok | partial | validation_failed | completed",
  "results": [
    {"field": "key", "ok": true,  "stored": true,  "value": "normalized"},
    {"field": "key", "ok": false, "stored": false, "error": "msg", "error_code": "code", "validator": "name"}
  ],
  "pruned": ["key"],     // fields made unreachable by branch settlement this call
  "ignored": ["key"],    // submitted but off-branch / unreachable; not processed
  "response_directive": "composed single chaining gate",
  "next_tool": "interview__next_field | interview__review",
  "system_message": "optional",
  "interview_complete": true   // only when a validator/post_processor terminates
}
```

The model already holds every prompt/guidance from the activation reference; `set_fields`
only needs to report what landed, what failed (with the user-facing error to relay), and
what to do next.

### 4.3 `next_field` slim return

```jsonc
{
  "ok": true,
  "next_field_key": "key",
  "prompt": "question to ask",       // included for safety (decision #5)
  "suggested_value": "from pre_processor",   // when present
  "response_directive": "Tell the user: <prompt>"
}
```

Drops `fields`, `awaiting_fields`, `field_keys`, `active_path_keys`, `guidance_page`,
`missing_required`. Guidance is **not** repeated — the model maps key → guidance via the
activation reference. **Lean target:** drop `prompt` too (key-only) once the base SOP's
key→prompt mapping is validated in practice.

## 5. Per-response processing discipline

Canonical contract for a single `set_fields` call carrying N submitted pairs. Formalizes
and tightens the existing loop ([engine.py:687-835](../../../jvagent/action/interview/engine.py)).

**Processing order:** field-definition order (NOT submission order), so branch-determining
fields settle before fields that depend on them.

For each field, in order:

1. **Reachability check** against the currently settled path. Unreachable ⇒ add to
   `ignored`, skip remaining steps for this field.
2. **pre_processor(s)** — may suggest/transform the value; contributes a directive to the
   queue. Side effects land in `session.context`.
3. **validator** — on failure: record in `results` with `ok:false` + the user-facing
   `error`, **skip store + post**, and **continue to the next field** (failure is isolated,
   never aborts siblings).
4. **store** — write to `session.fields`.
5. **post_processor(s)** — side effects; may set `next_tool` or `interview_complete`.
6. **settle branches** from the just-stored value — recompute reachability so later fields
   in the *same* call respect the path this value determined.

After the loop:
- Prune unreachable fields; collect into `pruned`.
- Compose ONE `response_directive` from the queued directives (existing `_compose_directives`).
- Compute `next_tool` (post_processor override wins, else chain hint).
- Completion check: if any validator/post_processor set `interview_complete` and there are
  no failures, clear the session honoring `retain_context_keys` and return
  `status: "completed"`.

**Status mapping:** all stored ⇒ `ok`; some stored + some failed ⇒ `partial`; none stored
due to validation ⇒ `validation_failed`; terminated ⇒ `completed`.

This is "order, store, settle, report." The current implementation is close; the work is
(a) guaranteeing definition-order iteration, (b) incremental branch settlement between
fields in one call, and (c) emitting the slim return.

## 6. Orchestrator decoupling

Both interview-specific references are removed.

**C1 — compound-extraction rule.** Delete
[skill_tasks.py:506-512](../../../jvagent/action/orchestrator/skill_tasks.py). The rule —
"for one user utterance, submit one `set_fields` call with all confident key/value pairs" —
**already lives in the base `SKILL.md` SOP** (`## Extraction pass`,
[SKILL.md](../../../jvagent/action/interview/SKILL.md)), which is re-surfaced each turn as
the PROCEDURE block. So C1 only removes the orchestrator's duplicate; no SOP addition
needed beyond a wording confirm.

**C2 — prep visualization.** Generalize `_emit_server_prep_tool_thoughts`
([orchestrator_interact_action.py:2405-2424](../../../jvagent/action/orchestrator/orchestrator_interact_action.py)).
Replace the `tool.startswith("interview__")` filter with a generic observation marker
(e.g. `entry.get("kind") == "server_prep"`). The marker is attached where prep
observations are created (the interview action's prep/activation hook and any future
skill's prep). Visualization keys off the marker, not a namespace.

**Acceptance gate:** `grep -REn 'interview|set_field|\bfield\b' jvagent/action/orchestrator/`
returns zero matches (outside comments/tests). Add this as a guard test.

## 7. Files touched

**Interview (`jvagent/action/interview/`)**
- `engine.py` — slim `set_fields`/`next_field` builders; enforce definition-order +
  incremental settlement; build `field_reference` in `_handle_start`.
- `responses.py` — new slim envelope builders; field-data keys removed from
  `set_fields`/`next_field`; retained only for activation/status.
- `procedure.py` + base `SKILL.md` — add compound-extraction rule to the SOP.
- `docs/multi-turn-flow.md`, `docs/thin-harness.md` — update the documented contract.

**Orchestrator (`jvagent/action/orchestrator/`)**
- `skill_tasks.py` — remove lines 507-512; ensure prep observations carry the
  `server_prep` marker.
- `orchestrator_interact_action.py` — generalize `_emit_server_prep_tool_thoughts`.

**Tests (`tests/action/`)**
- Update interview payload-shape assertions (several already in flight per `git status`).
- New: multi-response with partial validation failure — assert isolated failures, correct
  `results`, single directive.
- New: multi-response with mid-call branch settlement — assert later submitted-but-now-
  unreachable field lands in `ignored`/`pruned`.
- New: orchestrator generic-prep visualization test (no interview dependency).
- New: decoupling grep-guard test (acceptance gate in §6).

## 8. Risks / guardrails

- **Existing SOPs / skills reading `fields` off `set_fields`.** Audit `signup_interview`,
  `examples/example_interview`, and the `onboarding_interview` / `pre_alert_interview`
  fixtures; migrate any that read dropped keys to use the activation reference instead.
- **`next_field` key-only stretch.** Only adopt after confirming the base SOP reliably
  maps key → prompt; until then keep `prompt` in the return (decision #5).
- **Pruning recovery via pull.** Because `field_reference` is delivered once and recovered
  on demand via `interview__get_status` (decision #1), long interviews stay robust without
  any per-turn server push. The only requirement is that the base SOP keeps instructing the
  model to refresh via `get_status` when context thins — already present
  ([SKILL.md:23](../../../jvagent/action/interview/SKILL.md)); verify it survives the SOP edits.

## 9. Acceptance criteria

1. A single `set_fields` call with multiple values runs each through `pre → validator →
   post` in definition order, settling branches incrementally, and returns the §4.2 slim
   shape.
2. A partial-failure call stores valid values, isolates the failure, and returns one
   composed directive.
3. `field_reference` is present in the activation envelope and re-pullable via
   `interview__get_status`; `set_fields`/`next_field` returns contain no full field metadata
   and no per-turn server prep observation is introduced.
4. The orchestrator contains no `interview`/`field`/`set_field` literals (§6 gate) and the
   compound rule is served from the interview SOP.
5. Full test suite green; `pre-commit run --all-files` clean.
