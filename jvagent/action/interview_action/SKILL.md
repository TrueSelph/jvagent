---
name: interview_action_base
description: >-
  Framework-standard interview tool-loop procedure. Not a discoverable skill —
  inherited by action-backed interview skills via extends: action:jvagent/interview_action.
---

# Standard Interview Procedure

You conduct an interview by calling tools. Every step returns `ok:true/false` as the chaining gate. Read `fields`, `missing_required`, hook results (`pre_tools_results`, `post_tools_results`), and `next_tool` when present.

## Session rules

| Situation | Action |
| --------- | ------ |
| `active` or `review` | Session is open — use interview tools and `next_questions` |
| After **cancel** or **complete** | Call `use_skill` again with this skill to open a new session |

Never reuse field values from older chat turns unless the user repeats them in the **latest** message.

## Reply rules

Each tool returns one `response_directive` — do one thing that turn. **`response_directive` beats `next_questions` when they conflict.** If it starts with `Tell the user:`, paraphrase OK. If it is `Call interview__…`, call that tool only.

## Critical rules

1. **Do not enumerate fields in your head** — the active question comes from `next_questions[0]` after `interview__next_question` or from `response_directive`. Never invent questions or skip ahead of `missing_required`.
2. **Session starts when this skill is activated** (`use_skill`) — turn prep seeds the first question. Reply using `response_directive`; do not call `interview__next_question` again until after `set_field` returns `ok:true` (unless activation says `skip_to_review`).
3. **Chaining:** `interview__set_field(field, value)` → read `ok`; if `ok:false`, handle the error (`post_tools` do not run). Read `post_tools_results` before advancing. When `next_tool` is present, call it next.
4. **`interview__set_field` uses parameter `field`** — not `name`. Validation runs inside the tool; do not call validator functions directly.
5. **Optional fields:** call `interview__skip_field(field)` when the user declines.
6. **Never skip review** — call `interview__review()` before `interview__complete()` unless review sets `terminate: true`.
7. When `missing_required` is empty, call `interview__review()` then `interview__complete()` after user confirms.

## Core tools (`interview__*`)

- `interview__set_field(field, value)` — Validate and store; runs `post_tools` when configured.
- `interview__next_question()` — Next question; runs `pre_tools`. Returns `next_questions` and `response_directive`.
- `interview__get_field(field)` — Retrieve stored value.
- `interview__skip_field(field)` — Skip optional field.
- `interview__get_status()` — Full status dump.
- `interview__review()` — Review summary before complete.
- `interview__complete()` — Finalize after user confirms review.
- `interview__cancel()` — Cancel and clear session.
