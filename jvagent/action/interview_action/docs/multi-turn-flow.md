# Multi-Turn Interview Flow

How a skills-v2 interview progresses across user turns when the orchestrator drives the conversation via `interview__*` tools.

Turn flow assumes the **[thin harness principle](../../../../docs/thin-harness.md)** and [interview profile](thin-harness.md): the LLM drives each turn from the composed SOP; `InterviewAction` does not inject prep observations or auto-chain tools server-side.

## Roles

| Actor | Responsibility |
|-------|----------------|
| **User** | Sends messages each turn |
| **Orchestrator LLM** | Reads composed procedure (`SkillDoc.body`), calls tools, replies per `response_directive` |
| **InterviewAction** | Session CRUD, validation, hook execution, task tracking |
| **`SKILL.md` frontmatter `interview:`** | Field definitions, validators, hooks, tools, review/completion |
| **`SKILL.md` body** | Per-skill behavioral rules (standard procedure composed via extends) |
| **`scripts/custom_tools.py`** | Business logic referenced by `function:` names |

The LLM decides *which* question to ask and *when* to call tools. The action enforces validation, runs hooks, and returns structured observations the LLM must read before advancing.

## Session states

```
active → review → completed
  ↓         ↓
cancelled  cancelled
```

| Status | Meaning |
|--------|---------|
| `active` | Collecting fields |
| `review` | Summary shown; awaiting confirmation or edits |
| `completed` | `interview__complete()` finished; session cleared |
| `cancelled` | `interview__cancel()` or custom reset; session cleared |

State is stored on `InterviewSession.status` inside `conversation.context["interview"]`.

## Turn 0 — Skill activation

```
User message → Orchestrator selects use_skill("<skill_name>")
            → InterviewAction.on_skill_activate()
            → _handle_start(): create or resume session (no auto-store on activation)
            → INTERVIEW task created (owner: InterviewAction)
            → SKILL task created if locked-in: true
            → prepare_locked_skill_turn(): runtime-ready gate (no prep observations)
```

On every turn (including activation), the model follows base `SKILL.md` intent routing:

- Classify the user's message (answer, correct/update, multi-answer, cancel, etc.).
- Call the matching tool (`interview__set_fields`, `interview__next_question`, `interview__reset`, …).
- Chain follow-up tools per SOP (e.g. `set_fields` → `next_question` → reply).

`prepare_locked_skill_turn` only confirms the interview runtime is ready — it does **not** inject observations or directives.

### Anti-pattern: chat-only roleplay before activation

If the model skips `use_skill` and asks field prompts via `reply` alone, there is no session — `prune_turn_tools` hides `interview__*` tools and answers are not stored. Late `use_skill` cannot backfill values from earlier chat turns (utterance grounding). The user may need to repeat one field once. Fix: follow base SOP **Activation (session gate)** — `use_skill` first, then `interview__next_question`, then `reply`.

### Field extraction

The model extracts values from utterances and passes them to `interview__set_fields`. Validators are the hard gate; [`core/field_extractors.py`](../core/field_extractors.py) supports validation-time hints.

## Turn N — Typical collection turn

```mermaid
sequenceDiagram
    participant U as User
    participant O as Orchestrator LLM
    participant IA as InterviewAction
    participant H as custom_tools.py

    U->>O: Answer to current question
    O->>IA: interview__set_fields({field: value})
    IA->>H: validator (if configured)
    H-->>IA: valid / invalid
    alt valid
        IA->>H: post_tools (if configured)
        H-->>IA: post_tools_results
        IA-->>O: ok:true, fields, post_tools_results, response_directive
        O->>IA: interview__next_question()
        IA->>H: pre_tools (if configured)
        H-->>IA: pre_tools_results
        IA-->>O: next_questions, response_directive
        O->>U: Single reply per response_directive
    else invalid
        IA-->>O: ok:false, error
        O->>U: Re-ask using error text
    end
```

### Rules per turn

1. **One action per turn** — each tool returns one `response_directive`. Do not ask a question and call another tool in the same turn unless the directive says to call a tool only.
2. **Read `ok` first** — if `ok: false`, handle the error; `post_tools` did not run.
3. **Read hook results** — inspect `post_tools_results` / `pre_tools_results` before calling `next_question` or `review`.
4. **`response_directive` wins** — when it conflicts with `next_questions`, follow the directive.

## Turn-lock (`locked-in: true`)

When a skill declares `locked-in: true`, the orchestrator stays in the active skill flow until the interview completes or cancels. Generic hooks on `InterviewAction` (not interview-specific orchestrator code):

| Hook | Purpose |
|------|---------|
| `skill_runtime_ready(skill_name, visitor)` | Session + contract loaded |
| `prepare_locked_skill_turn(skill_name, visitor)` | Runtime-ready gate only — no prep observations |
| `prune_turn_tools(tools, visible, visitor)` | Hide interview tools when runtime not ready |

This keeps multi-turn interviews on-rails without hardcoding interview logic in the orchestrator.

## Optional fields

For `required: false` questions:

- User declines → `interview__skip_field(field)` then `interview__next_question()`.
- Do not call `interview__review()` while optional fields remain in `next_questions` unless the procedure explicitly allows it.

## Branching without a state machine

Branching is **procedure-driven**, not graph-evaluated:

| Mechanism | How branching works |
|-----------|---------------------|
| `post_processor` | Returns `skip_to_review: true` → LLM calls `interview__review()` |
| Custom validator | Returns `interview_complete: true` → stop; post-processors skipped |
| Review handler | Returns `terminate: true` → deliver message; no `interview__complete()` |
| `session.context` | Post-processors set flags (e.g. `escalate`, `otp_pending`) read by later hooks or SKILL.md |
| Skill tools | e.g. `send_otp` — LLM calls `{skill}__{tool}` explicitly |
| Custom reset | `handlers.reset` — LLM calls `interview__reset()` |
| `fields[].branches` | `when` / `goto` / `else` — declarative routing after field save |

Document branches in `SKILL.md` and implement side effects in hooks.

### Branch path invalidation (corrections)

When the user corrects a field that determines a branch (`fields[].branches`), the runtime recomputes the **full active path** (start → terminal field) from stored values and **prunes only off-path fields** — answers on the new path that remain valid (e.g. `contact` after `user_type` premium→standard, or `phone_number` after email branch pivot) are preserved.

- Pruned field names are recorded in `session.context.pruned_fields` and may appear as `pruned_fields` on `interview__set_fields` responses.
- Prune also clears `skipped_fields` entries and stale `question_presented` / `field_suggestion` scratch keys for pruned fields.
- `resolve_next_question_name` still returns the first unanswered field on the active path; use `missing_required` / `next_questions` after a correction — do not assume every spec field is still collected.

## Review and completion turns

```
All required fields collected (+ optional handled)
  → interview__review()
  → built-in summary OR `handlers.review` (confirmation framing via `review_confirmation_directive` or `confirm: auto`)
  → if terminate: true → stop (escalation path)
  → else user confirms → interview__complete()
  → completion handler → `clear_interview_context()` (honors `retain_context_keys`), INTERVIEW task closed
```

If the user wants to edit during review, call `interview__set_fields` for the field(s) and re-run `interview__review()`.

## Cancel and restart

| Path | When | Effect |
|------|------|--------|
| `interview__cancel()` | User explicitly cancels (default skills) | Clear session, cancel tasks |
| `interview__reset()` | User wants to start over (default) | Clear + re-init from first question |
| `interview__reset()` + `handlers.reset` | Skill overrides reset (e.g. onboarding) | Routes to custom handler — may cancel-and-exit instead of restart |
| New session after complete/cancel | User starts again | Call `use_skill("<name>")` again |

Skills that replace cancel semantics may set `disabled-tools: [interview__cancel]` and set `handlers.reset`.

## Dual task model

| Task | Owner | Purpose |
|------|-------|---------|
| SKILL | Orchestrator skill runtime | Turn-lock for `locked-in: true` skills |
| INTERVIEW | `InterviewAction` | Progress tracking for UI / task store |

Both may be active during an interview. Custom completion handlers often close the INTERVIEW task and may persist profile data to the SKILL task before completing it.

## Reference procedure

The framework-standard tool loop lives in [`../SKILL.md`](../SKILL.md) and is prepended to each interview skill's `SkillDoc.body` at discovery. Per-skill exceptions belong in the custom `SKILL.md` body — see [`../docs/skill_custom_instructions.md`](../docs/skill_custom_instructions.md).

Examples:

- [`examples/example_interview/SKILL.md`](../examples/example_interview/SKILL.md) — reference custom rules
- zoon-ai `onboarding_interview/`, `pre_alert_interview/` — production behavioral rules
- jvagent example app `signup_interview/` — demo signup flow
