---
name: interview_base
description: >-
  Framework-standard interview tool-loop procedure. Not a discoverable skill —
  inherited by action-backed interview skills via extends: action:jvagent/interview.
task-lock: true
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

Conduct interviews by calling `interview__*` tools. The server does not steer turns — you classify intent, extract values, and chain tools ([thin harness](docs/thin-harness.md)).

Context contract: activation via `use_skill` is the single field catalog — `field_reference` (every field's `key`, `prompt`, `guidance`, `required`) plus `start_field` (where to begin). Nothing else duplicates that data. `interview__set_fields` returns only per-field outcomes (`results`: saved-or-not + the value you submitted + why, when not saved) and `response_directive`; `interview__next_field` returns `{key, prompt}` + directive. Re-pull `field_reference` any time with `interview__get_status`.

## Session gate

Sessions open only via `use_skill(<skill_name>)`.

1. **No session → no field prompts in `reply`.** Prompts come from `interview__next_field` after activation.
2. **Activation:** Read `field_reference` (the full field catalog) and `start_field` from the activation observation. Extract from the full latest message and call `interview__set_fields` once with every applicable key/value found. The active question key is a minimum anchor, not an exclusive whitelist. If required fields remain after storage → `interview__next_field` → `reply`. Otherwise continue per `next_tool` / `response_directive`.
3. Only the activation message counts — values from earlier chat turns are not stored.
4. After **cancel** or **complete**, call `use_skill` again to start fresh.
5. Never reuse field values from older turns unless the user repeats them in the **latest** message.

## Intent → tool

| Intent | Signals | Tool |
| ------ | ------- | ---- |
| Start (no session) | User wants this interview skill | `use_skill` → activation chain |
| Answer | Supplies info for current question | `interview__set_fields` |
| Multi-answer | Several fields in one message | `interview__set_fields` with every matching key from the same utterance |
| Correct / update | Names a stored field, "actually…", "wrong" | `interview__set_fields` same turn — do not ask to confirm first |
| Decline optional | "skip", "no thanks" on optional field | `interview__skip_field` |
| Cancel / stop | "cancel", "never mind", "quit" | `interview__cancel` |
| Start over | "start over", "restart", "try again" | `interview__reset` (not `cancel`) |
| Confirm (`confirm: manual`) | yes / looks good at review | `interview__complete` after `interview__review` |

Use `interview__get_status` for progress. On `NO_SESSION`, activate then `interview__next_field`.

## Extraction pass (required before `set_fields`)

- Read the full latest user utterance once before building `set_fields` args.
- Map all confident values in that utterance to canonical keys from `field_reference[].key` (matched by each field's `prompt`/`guidance`).
- Do not invent, alias, prefix, pluralize, or rename keys. Copy the exact key string from `field_reference[].key` — submit it verbatim as it appears there (e.g. if the catalog key is `email`, send `email`, not `user_email` or `email_address`; if it is `available_times`, do not shorten to `availability`). Never assume a key from another skill.
- Submit one initial `interview__set_fields` call containing every extracted key/value from that utterance.
- Treat the active/pending field as an anchor only; do not treat it as an exclusive key whitelist.
- If validation fails, retry with corrected keys/values from the same utterance or the next user clarification.

## Chaining

- `ok:false` → handle the error; post-processors did not run for failed fields.
- **Forward answer:** `set_fields` → `next_field` when `missing_required` non-empty → `reply`.
- **Anti-drip rule:** For one user utterance, make one initial `set_fields` call with all extracted keys; do not split the same message into one-field calls unless retrying after validation failures.
- **Correction:** `set_fields` → `next_field` if more required fields remain, else `review` when at review stage. After review corrections, call `review` again before asking for summary confirmation.
- **`response_directive` beats `next_tool`.** When it starts with `Tell the user:`, reply unless it explicitly says `Call interview__…`.
- **Unknown key recovery:** on `error_code: UNKNOWN_FIELD` (or failed fields with that code), read `system_message` / `system_messages_queue` / `failed_fields[].system_message` for the canonical key mapping, then retry one corrective `set_fields` batch with corrected canonical keys.
- Do not claim the process is finished until `interview__complete()` succeeds.

## Branching

Fields may branch on stored values. Changing an upstream answer prunes off-path stored fields. The server sequences fields for you — follow `start_field`, then `interview__next_field` and `response_directive`; do not assume every field in the spec is still on the active path.

When a correction pivots branches: include all co-mentioned branch fields in one `set_fields` call. The runtime settles branch path after batch processing and prunes off-path values.

After successful `set_fields`, read `pre_tools_results`, `post_tools_results`, and `response_directive` (or `response_directives_queue`) before chaining `next_field` or `review`.

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
- Always place the question prompt from `next_field` in its own line for emphasis. Place any hints or notes in their own separate line.
- Do not store filler or acknowledgements as answers — validators are the gate; re-ask using `error` from failed `set_fields`.
- Fields listed in `skipped_fields` (carried on `set_fields`, `next_field`, `skip_field`, and `get_status`) were declined — never re-prompt them, including while correcting other answers.
- Map user answers to canonical keys from `field_reference[].key` (the activation catalog) — never invent or alias field keys.
- `set_fields` args: `{"fields": {"field_key": "value"}}` — never put field keys at the top level.
- `skip_field` args: `{"field_key": "field_name"}`.
- Full field catalog: `interview__get_status` returns `field_reference` on demand; activation already includes `field_reference` and `start_field`.
- When in `review`, you must **list** responses with **bold** keys, for clarity.
