# Multi-Turn Interview Flow

How a skills-v2 interview progresses across user turns when the orchestrator drives the conversation via `interview__*` tools.

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
            → _handle_start(): create/resume session, seed fields, run post_tools for seeds
            → INTERVIEW task created (owner: InterviewAction)
            → SKILL task created if locked-in: true
```

On every turn (including activation), `prepare_locked_skill_turn` runs **message evaluation** on the user's latest utterance:

- **`interview__message_evaluation` observation** when applicable entities are found — model calls `interview__set_field` with a candidate, then replies using the merged `response_directive`.
- **`interview__next_question` observation** when no applicable entities (empty utterance, intent-only message, or no valid candidates) — reply using the scripted question.

### Entity candidate registry

[`core/field_extractors.py`](../core/field_extractors.py) surfaces validator-keyed candidates for evaluation (email, phone, names, tracking numbers, training slots, etc.). Evaluation pre-validates candidates; the model performs extraction via `set_field`.

## Turn N — Typical collection turn

```mermaid
sequenceDiagram
    participant U as User
    participant O as Orchestrator LLM
    participant IA as InterviewAction
    participant H as custom_tools.py

    U->>O: Answer to current question
    O->>IA: interview__set_field(field, value)
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
| `prepare_locked_skill_turn(skill_name, visitor)` | Mechanical `interview__next_question` seed when no question is already presented (`CTX_QUESTION_PRESENTED`) |
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
| `post_tools` | Returns `skip_to_review: true` → LLM calls `interview__review()` |
| Custom validator | Returns `interview_complete: true` → stop; post_tools skipped |
| Review handler | Returns `terminate: true` → deliver message; no `interview__complete()` |
| `session.context` | Post-tools set flags (e.g. `escalate`, `otp_pending`) read by later hooks or SKILL.md |
| LLM custom tools | e.g. `send_otp` — LLM calls `{skill}__{tool}` explicitly |
| Custom reset | `interview.reset.function` — LLM calls `interview__reset_interview()` |

Document branches in `SKILL.md` and implement side effects in hooks.

## Review and completion turns

```
All required fields collected (+ optional handled)
  → interview__review()
  → built-in summary OR `interview.review` handler (confirmation framing via `review_confirmation_directive`)
  → if terminate: true → stop (escalation path)
  → else user confirms → interview__complete()
  → completion handler → `clear_interview_context()` (honors `retain_context_keys`), INTERVIEW task closed
```

If the user wants to edit during review, call `interview__set_field(field, new_value)` and re-run `interview__review()`.

## Cancel and restart

| Path | When | Effect |
|------|------|--------|
| `interview__cancel()` | User explicitly cancels (default skills) | Clear session, cancel tasks |
| `interview__reset_interview()` | User wants to start over (default) | Clear + re-init from first question |
| `interview__reset_interview()` + `interview.reset` | Skill overrides reset (e.g. onboarding) | Routes to custom handler — may cancel-and-exit instead of restart |
| New session after complete/cancel | User starts again | Call `use_skill("<name>")` again |

Skills that replace cancel semantics may set `disabled-tools: [interview__cancel]` and implement `interview.reset.function`.

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
