---
name: interview_base
description: >-
  Framework-standard interview tool-loop procedure. Not a discoverable skill —
  inherited by action-backed interview skills via extends: action:jvagent/interview.
allowed-tools:
  - interview__set_fields
  - interview__skip_field
  - interview__next_field
  - interview__get_status
  - interview__review
  - interview__complete
  - interview__cancel
  - interview__reset
---

# Standard Interview Procedure

Conduct interviews by calling `interview__*` tools. Each response includes `ok`, `fields`, `missing_required`, `awaiting_fields`, `field_awareness`, `results`, hook outputs (`pre_tools_results`, `post_tools_results`), `next_tool`, and `response_directive`. Prior turns may also include `[EVENT]` lines with the same `field_awareness` text. The server does not steer turns — you classify intent, extract values, and chain tools ([thin harness](docs/thin-harness.md)).

## Session gate

Sessions open only via `use_skill(<skill_name>)`.

1. **No session → no field prompts in `reply`.** Prompts come from `interview__next_field` after activation.
2. **Activation:** Read `field_awareness` and `awaiting_fields` from the activation observation — map extractions to the quoted `field_key` only; never invent keys (e.g. `full_name` when the key is `user_name`). If the latest message has extractable values → `interview__set_fields` → `interview__next_field` when `missing_required` is non-empty → `reply`. Otherwise → `interview__next_field` first → `reply` from tool output (not from memory).
3. Only the activation message counts — values from earlier chat turns are not stored.
4. After **cancel** or **complete**, call `use_skill` again to start fresh.
5. Never reuse field values from older turns unless the user repeats them in the **latest** message.

## Intent → tool

| Intent | Signals | Tool |
| ------ | ------- | ---- |
| Start (no session) | User wants this interview skill | `use_skill` → activation chain |
| Answer | Supplies info for current question | `interview__set_fields` |
| Multi-answer | Several fields in one message | `interview__set_fields` with every matching key |
| Correct / update | Names a stored field, "actually…", "wrong" | `interview__set_fields` same turn — do not ask to confirm first |
| Decline optional | "skip", "no thanks" on optional field | `interview__skip_field` |
| Cancel / stop | "cancel", "never mind", "quit" | `interview__cancel` |
| Start over | "start over", "restart", "try again" | `interview__reset` (not `cancel`) |
| Confirm (`confirm: manual`) | yes / looks good at review | `interview__complete` after `interview__review` |

Use `interview__get_status` for progress. On `NO_SESSION`, activate then `interview__next_field`.

## Chaining

- `ok:false` → handle the error; post-processors did not run for failed fields.
- **Forward answer:** `set_fields` → `next_field` when `missing_required` non-empty → `reply`.
- **Correction:** `set_fields` → `next_field` if more required fields remain, else `review` when at review stage. After review corrections, call `review` again before asking for summary confirmation.
- **`response_directive` beats `next_tool`.** When it starts with `Tell the user:`, reply unless it explicitly says `Call interview__…`.
- Do not claim the process is finished until `interview__complete()` succeeds.

## Branching

Fields may branch on stored values. Changing an upstream answer prunes off-path stored fields. Use `awaiting_fields`, `missing_required`, and `next_field` — do not assume every field in the spec is still on the active path.

When a correction pivots branches: store the pivot field first; batch co-mentioned downstream branch fields in one `set_fields` when the user supplies them together.

After successful `set_fields`, read `post_tools_results` and `response_directive` before chaining `next_field` or `review`.

## Confirm mode

`interview__get_status` and `interview__review` expose `confirm`: `manual` (default) or `auto`.

| Mode | When `missing_required` is empty |
| ---- | -------------------------------- |
| `manual` | `review` → wait for user confirmation → `complete` |
| `auto` | `review` → `complete` in the same turn (no "does this look correct?") |

Review handlers with `terminate: true` end without `complete`.

## Reply rules

- One primary action per turn.
- Ask questions only from `next_field` / `response_directive` supplied **this turn**.
- Do not store filler or acknowledgements as answers — validators are the gate; re-ask using `error` from failed `set_fields`.
- Map user answers to the `field_key` in `field_awareness` / `awaiting_fields` from the latest tool observation — never invent field keys.
- `set_fields` args: `{"fields": {"field_key": "value"}}` — never put field keys at the top level.
- `skip_field` args: `{"field_key": "field_name"}`.
- Full schema catalog: `interview__get_status` (`field_definitions`); activation returns `awaiting_fields` only.
