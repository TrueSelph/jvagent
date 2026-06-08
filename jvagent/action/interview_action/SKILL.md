---
name: interview_action_base
description: >-
  Framework-standard interview tool-loop procedure. Not a discoverable skill —
  inherited by action-backed interview skills via extends: action:jvagent/interview_action.
allowed-tools:
  - interview__set_field
  - interview__get_field
  - interview__skip_field
  - interview__next_question
  - interview__get_status
  - interview__review
  - interview__complete
  - interview__cancel
  - interview__reset_interview
---

# Standard Interview Procedure

You conduct an interview by calling tools. Every step returns `ok:true/false` as the chaining gate. Read `fields`, `missing_required`, hook results (`pre_tools_results`, `post_tools_results`), and `next_tool` when present.

**All skills that extend this procedure inherit the rules below** — Answer quality gate, Intent routing, reply/chaining rules, and cancel/start-over handling. Do not rely on per-skill custom instructions for these; they are the standard ruleset for every interview.

## Session rules

| Situation | Action |
| --------- | ------ |
| `active` or `review` | Session is open — use interview tools and `next_questions` |
| After **cancel** or **complete** | Call `use_skill` again with this skill to open a new session |

Never reuse field values from older chat turns unless the user repeats them in the **latest** message.

## Reply rules

Each tool returns one `response_directive` — do one thing that turn. **`response_directive` beats `next_questions` when they conflict.** If it starts with `Tell the user:`, paraphrase OK. If it is `Call interview__…`, call that tool only.

When `response_directive` starts with **`Tell the user:`**, reply to the user only — do **not** call another tool that turn unless the directive explicitly says `Call interview__…`.

## Answer quality gate

Before every `interview__set_field` call, decide whether the user's **latest message** substantively answers the active question.

1. Read `next_questions[0]` — its `question`, `description`, and `name`.
2. Compare the latest user message to what that field is asking for. Use `description` as acceptance criteria when present.
3. **Do not call `interview__set_field`** when the message is:
   - An acknowledgement or filler (e.g. "ok", "sure", "got it", "yeah yeah")
   - A greeting or small talk unrelated to the question
   - Off-topic or answering a different field
   - Clearly not the kind of information the field expects
4. On those turns: **reply only** — politely say you still need their answer and re-ask the active question. Do not call interview tools that turn.
5. **Exception:** fields that expect yes/no (validator `yes_no`) — acknowledgements may be valid answers there. For optional fields, use `interview__skip_field` when the user declines.

| Active field type | User says | Action |
| ----------------- | --------- | ------ |
| Name / identity question | "Ok Ok" | Reply only — not a substantive answer |
| Name / identity question | "Jane Doe" | `interview__set_field` |
| Choice from a presented list | "sure" | Reply only — not a selection |
| Optional field | "no thanks" / "skip" | `interview__skip_field` |

Per-field acceptance criteria live in `next_questions[0].description` — use them together with this gate.

### Message evaluation (every turn)

Turn prep runs **message evaluation** on the user's **latest message** — including the message that triggered skill activation. Read the `interview__message_evaluation` observation when present.

1. When `applicable` lists fields with `candidates`, call `interview__set_field` for the **first missing applicable field**, using a candidate value you extract from the message.
2. On `ok:true`, the response includes `next_questions` and a `Tell the user:` `response_directive` — **reply only**; do not call `interview__next_question` (the server already advanced).
3. When `applicable` is empty, use the `interview__next_question` observation — reply using its `response_directive`; do not call `set_field` with the full utterance.
4. Intent-only messages (e.g. "sign me up" without extractable entities) have empty `applicable` — present the scripted next question from the observation.
5. Multiple inline entities in one message: extract the **first missing applicable field** this turn; call `set_field` again only if evaluation still lists another applicable field after a successful store.

## Intent routing

Before any tool call, classify the user's **latest message** into one intent. Pick **one primary tool** for that turn — do not chain unrelated tools.

| User intent | Signals (examples) | Tool | Do not call |
| ----------- | ------------------ | ---- | ----------- |
| **Answer** the active question | Supplies the requested information | `interview__set_field` (after Answer quality gate) | `cancel`, reset tool, `next_question` |
| **Cancel** / stop / quit | "cancel", "stop", "I want to cancel", "never mind", "forget it" | `interview__cancel` | reset tool, `set_field`, `next_question` — session ends; confirm and stop |
| **Start over** (same interview) | "start over", "restart", "try again from the beginning" | `interview__reset_interview`, or a skill-specific reset tool if one replaced it | `cancel` when user asked to restart, not leave |
| **Decline optional field** | "skip", "no thanks" on optional question | `interview__skip_field` | `set_field` with empty filler |

After **`interview__cancel`**: the session is closed. Reply with the tool's `response_directive` only — do **not** call `interview__next_question`, `set_field`, or a reset tool that turn.

### Reset tool (`interview__reset_interview`)

The base reset tool clears progress and restarts the active interview from the first question.

- Use only for **start over** intent — never for cancel/stop/quit (`interview__cancel` handles those).
- Call **one** reset tool that turn; follow its `response_directive` only.
- Do **not** chain `interview__next_question` or other interview tools in the same turn — the reset handler prepares the next step.

A skill may **override** the base reset by declaring `interview.reset` in frontmatter (same pattern as `review` / `completion`):

```yaml
interview:
  reset:
    function: reset_onboarding
    description: Custom cancel-and-exit behavior for this skill.
```

Implement `reset_onboarding` in `scripts/custom_tools.py`. The model still calls `interview__reset_interview()` — the foundation routes to your handler when `reset.function` is set.

## Critical rules

1. **Do not enumerate fields in your head** — the active question comes from `next_questions[0]` after `interview__next_question` or from `response_directive`. Never invent questions or skip ahead of `missing_required`.
2. **Every turn starts with message evaluation** — turn prep injects either `interview__message_evaluation` or `interview__next_question`. Follow that observation's directive; never reply with a field question without reading the prep observation first.
3. **After `set_field`:** read `ok`; if `ok:false`, handle the error (`post_tools` do not run). Read `post_tools_results` before advancing. On `ok:true`, a `Tell the user:` `response_directive` with `next_questions` means the next question is ready — **reply only**; do not call `interview__next_question` in the same turn. Call `interview__next_question` only when a tool returns `Call interview__next_question.` with no `next_questions` yet (e.g. after `skip_field` when the response still chains mechanically).
4. **`interview__set_field` uses parameter `field`** — not `name`. Validation runs inside the tool; do not call validator functions directly.
5. **Optional fields:** call `interview__skip_field(field)` when the user declines.
6. **Never skip review** — call `interview__review()` before `interview__complete()` unless review sets `terminate: true`.
7. When `missing_required` is empty, call `interview__review()` then `interview__complete()` after user confirms.

## Prep observations (server-injected, not callable)

Turn-lock prep may inject these **before** your first tool decision each turn. They are **not** in `allowed-tools` — do not try to call them.

- `interview__message_evaluation` — Applicable fields and validator-checked `candidates` from the user's latest message. Follow the Message evaluation rules above; call `interview__set_field` for the first missing applicable field when `applicable` is non-empty.
- `interview__next_question` — Scripted next question when no extractable entities matched. Reply using `response_directive` only.

## Core tools (`interview__*`)

- `interview__set_field(field, value)` — Validate and store; runs `post_tools` when configured. Only call when the latest message passes the Answer quality gate.
- `interview__next_question()` — Next question; runs `pre_tools`. Returns `next_questions` and `response_directive`.
- `interview__get_field(field)` — Retrieve stored value.
- `interview__skip_field(field)` — Skip optional field when the user declines.
- `interview__get_status()` — Full status dump.
- `interview__review()` — Review summary before complete.
- `interview__complete()` — Finalize after user confirms review.
- `interview__cancel()` — Cancel and close the session. Use for stop/quit/cancel intent — not for start over.
- `interview__reset_interview()` — Clear progress and restart from the first question. Use for start-over intent — not for cancel.
