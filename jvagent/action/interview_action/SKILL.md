---
name: interview_action_base
description: >-
  Framework-standard interview tool-loop procedure. Not a discoverable skill —
  inherited by action-backed interview skills via extends: action:jvagent/interview_action.
allowed-tools:
  - interview__set_fields
  - interview__get_fields
  - interview__skip_field
  - interview__next_question
  - interview__get_status
  - interview__review
  - interview__complete
  - interview__cancel
  - interview__reset
---

# Standard Interview Procedure

You conduct an interview by calling tools. Every step returns `ok:true/false` as the chaining gate. Read `fields`, `missing_required`, `results`, hook outputs (`pre_tools_results`, `post_tools_results`), and `next_tool` / `response_directive` when present.

**Harness design:** the server does not steer your turns — no prep injections, no auto-stored fields on activation. You classify intent, extract values, and chain tools per this procedure. Platform principle: [`docs/thin-harness.md`](../../../docs/thin-harness.md). Interview profile: [`docs/thin-harness.md`](docs/thin-harness.md).

**All skills that extend this procedure inherit the rules below** — activation gate, intent routing, reply/chaining rules, and cancel/start-over handling.

## Activation (session gate)

There is no `interview__init` tool. Sessions open only when the orchestrator calls `use_skill(<skill_name>)`, which runs `on_skill_activate` and creates `conversation.context["interview"]`.

1. **No session → no interview questions via `reply` alone.** Field prompts live in `interview.fields[].prompt` and are surfaced through `interview__next_question` after `use_skill` opens the session. Do not role-play the interview in chat.
2. **First entry:** when user intent matches a listed interview skill and there is no active session (`interview__get_status` or context) → call `use_skill(<skill_name>)` **before** asking any field question.
3. **Activation turn chain:**
   - If the latest message contains extractable answers → `interview__set_fields` (this message only) → `interview__next_question` if `missing_required` is non-empty → `reply`.
   - Otherwise → `interview__next_question` first → `reply` using tool output (do not paraphrase prompts from memory).
4. **Late activation:** values from chat turns **before** `use_skill` are not stored; only the activation message counts (see utterance grounding below).
5. **One locked interview at a time** when turn-lock applies; do not run parallel interview flows in plain chat.

## Session rules

| Situation | Action |
| --------- | ------ |
| User wants an interview skill, **no active session** | `use_skill(<name>)` then activation chain above |
| `active` or `review` | Session is open — use interview tools |
| After **cancel** or **complete** | Call `use_skill` again with this skill to open a new session |

Never reuse field values from older chat turns unless the user repeats them in the **latest** message.

## Turn loop (every user message)

1. Read the user's **latest message**.
2. Classify **one primary intent** (table below).
3. Call **one tool** for that intent (or `reply`/`respond` if clarification is needed).
4. Read tool `ok` and `response_directive`; chain another tool **only** when `response_directive` says `Call interview__…` or the SKILL procedure below requires it.

## Intent routing

| User intent | Signals (examples) | Tool | Do not call |
| ----------- | ------------------ | ---- | ----------- |
| **Start interview** | Signup, verify, tracking flow, etc.; matching AVAILABLE SKILL; no session | `use_skill` → `interview__next_question` or activation `set_fields` | `reply` with field prompts; `interview__*` before session |
| **Answer** active question | Supplies info for the current question | `interview__set_fields` | `cancel`, `reset`, unrelated tools |
| **Correct / update** prior answer | "change my email", "actually…", "wrong", names a stored field | `interview__set_fields` for that field | Treating as off-topic |
| **Multi-answer** (e.g. activation) | User gives several fields at once | `interview__set_fields` with all extractable fields | Storing filler or acknowledgements |
| **Cancel** / stop / quit | "cancel", "stop", "never mind", "forget it" | `interview__cancel` | `reset`, `set_fields`, `next_question` |
| **Start over** (same interview) | "start over", "restart", "try again from the beginning" | `interview__reset` | `cancel` when user asked to restart |
| **Decline optional field** | "skip", "no thanks" on optional question | `interview__skip_field` | `set_fields` with empty filler |
| **Confirm review** | yes / looks good (at review) | `interview__complete` after `interview__review` if needed | Skipping review when required — **only when `confirm` is `manual`** |

Use `interview__get_status` or `interview__get_fields` when you need to see what is already stored.

## Confirm mode

`interview__get_status` and `interview__review` include `confirm`: `manual` (default) or `auto`.

| Mode | Behavior |
| ---- | -------- |
| **`manual`** | After `interview__review`, show the summary and wait for explicit user confirmation before `interview__complete`. |
| **`auto`** | When `missing_required` is empty, call `interview__review()` then `interview__complete()` in the **same turn** — do not ask "does this look correct?". Review may set `next_tool: interview__complete`. |

Skills with review handlers that return `terminate: true` are unchanged — auto-confirm does not apply on the terminate path.

## Corrections

- Any stored field may be updated at any time via `interview__set_fields` — mid-interview or at review.
- Corrections are **not** off-topic; they are a first-class intent.
- At **review**: after correcting, call `interview__review()` again to refresh the summary before asking for confirmation.

## Chaining

- After successful `interview__set_fields` for a **forward answer**: if `missing_required` is non-empty, call `interview__next_question`, then reply to the user.
- After successful `interview__set_fields` for a **correction**: acknowledge the update, then continue (call `interview__next_question` if you need the active question).
- After `interview__skip_field`: follow `response_directive`; call `interview__next_question` when the response says to.
- When `response_directive` starts with **`Tell the user:`**, reply to the user — chain another tool only if the directive explicitly says `Call interview__…`.

## Reply rules

Each tool returns one `response_directive` — prefer one primary action per tick. **`response_directive` beats `next_questions` when they conflict.**

Do not store obvious filler (e.g. "ok ok" as a full name) — validators are the hard gate; ask again when validation fails.

Never ask `fields[].prompt` text via `reply`/`respond` unless `interview__next_question` (or `response_directive`) supplied it **this turn** with an active session.

## Reset tool (`interview__reset`)

- Use only for **start over** intent — never for cancel/stop/quit (`interview__cancel` handles those).
- Call **one** reset tool that turn; follow its `response_directive`.

A skill may override reset by declaring `handlers.reset` in frontmatter:

```yaml
interview:
  handlers:
    reset: reset_onboarding
```

Implement the handler in `scripts/custom_tools.py`. The model still calls `interview__reset()` — the foundation routes to your handler when `handlers.reset` is set.

## Critical rules

1. **Active question** — call `interview__next_question` when you need `next_questions[0]`; do not invent questions.
2. **After `set_fields`:** read `ok` and per-field `results`; if `ok:false`, handle errors (`post_processor` hooks do not run for failed fields).
3. **`interview__set_fields`** accepts a `fields` map `{field_name: value}`; single-field `field`/`value` is a deprecated alias.
4. **Optional fields:** call `interview__skip_field(field)` when the user declines.
5. **Never skip review** — call `interview__review()` before `interview__complete()` unless review sets `terminate: true`.
6. **`manual` confirm:** when `missing_required` is empty, call `interview__review()` then `interview__complete()` after user confirms.
7. **`auto` confirm:** when `missing_required` is empty and `confirm` is `auto`, call `interview__review()` then `interview__complete()` in the same turn without asking for confirmation.
8. **Session required:** `interview__*` tools require an active session. On `NO_SESSION`, call `use_skill` then `interview__next_question` — do not compensate with chat-only field questions.

## Core tools (`interview__*`)

- `interview__set_fields(fields)` — Validate and store one or more fields; runs `post_processor` hooks per field when configured.
- `interview__get_fields(fields?)` — Read stored values; omit `fields` for all collected.
- `interview__next_question()` — Next question; runs `pre_processor` hooks.
- `interview__get_status()` — Full session dump.
- `interview__skip_field(field)` — Skip optional field.
- `interview__review()` — Review summary before complete.
- `interview__complete()` — Finalize after user confirms review.
- `interview__cancel()` — Cancel and close session.
- `interview__reset()` — Clear progress and restart, or custom reset handler.

Deprecated aliases: `interview__set_field`, `interview__get_field` — prefer `set_fields` / `get_fields`.
