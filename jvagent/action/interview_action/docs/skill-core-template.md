# Interview Skill — Core Instructions (template)

Include this block in every interview `SKILL.md` under **Core instructions**. Add domain-specific content only under **Custom instructions**.

## Core instructions

### How this interview works

You conduct an interview by calling tools. Every step returns `ok:true/false` as the chaining gate. Read `fields`, `missing_required`, and hook results (`pre_tools_results`, `post_tools_results`). When `next_tool` is present, call that tool next.

### Session rules

| Situation | Action |
| --------- | ------ |
| `active` or `review` | Session is open — use interview tools |
| After **cancel** or **complete** | Call `use_skill` again to open a new session |

Never reuse field values from older chat turns unless the user repeats them in the **latest** message.

### Reply rules

Each tool returns one `response_directive` — do one thing that turn. **`response_directive` beats `next_questions` when they conflict.** If it starts with `Tell the user:`, paraphrase OK. If it is `Call interview__…`, call that tool only.

### Critical rules

1. Session starts when this skill is activated (`use_skill`) — turn prep seeds the first question. Reply using `response_directive`; do not call `interview__next_question` again until after `set_field` returns `ok:true` (unless activation says `skip_to_review`).
2. **Chaining:** `set_field` → read `ok`; if `ok:false`, handle error. Read `post_tools_results` before advancing.
3. **`interview__set_field` uses parameter `field`** — not `name`.
4. **Never skip review** — call `interview__review()` before `interview__complete()` unless review sets `terminate: true`.

### Core tools

- `interview__set_field(field, value)` — Validate and store.
- `interview__next_question()` — Next question; runs `pre_tools`.
- `interview__get_field(field)` — Retrieve stored value.
- `interview__skip_field(field)` — Skip optional field.
- `interview__get_status()` — Full status dump.
- `interview__review()` — Review summary.
- `interview__complete()` — Finalize.
- `interview__cancel()` — Cancel session.
