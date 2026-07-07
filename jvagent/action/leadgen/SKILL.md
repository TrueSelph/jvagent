---
name: leadgen_base
description: >-
  Framework-standard leadgen capture procedure. Not a discoverable skill —
  inherited by action-backed leadgen skills via extends: action:jvagent/leadgen.
always-active: false
allowed-tools:
  - leadgen__capture
  - leadgen__retrieve
  - leadgen__status
  - leadgen__sync
---

# Standard Leadgen Procedure

Gather lead details naturally throughout the conversation. The server validates, persists, and auto-syncs when configured — you decide **when** to capture, not how storage works ([thin harness](docs/thin-harness.md)).

## When to call `leadgen__capture`

Call whenever the user's **latest message** provides or refuses lead information:

- Name, company, email, phone, project details, product interest
- **Refusals** — use `decline_value` from `field_reference` (e.g. `email="N/A"`, `organization="Personal"`)
- Corrections — capture updated values in the same turn

Do **not** call with empty arguments or when nothing changed.

## When to call `leadgen__retrieve`

Call when you need profile context but the user did not provide new data this turn:

- Start of conversation (after greeting)
- Before gap-fill questions
- After `leadgen__capture` in the same turn (retrieve is optional — capture response includes `missing_fields`)

## Gap-fill

Use `missing_fields` and `gap_fill_priority` from tool responses. Batch related asks naturally (e.g. name + phone together). Never expose raw field keys or YAML to the user.

## Sync

When `sync.mode` is `on_capture` (default), the server auto-syncs after successful capture when thresholds are met. **Do not** call `leadgen__sync` unless `sync.mode` is `manual`.

Never mention sync status to the user — continue the conversation naturally.

## Acknowledge naturally

- ✅ "Got it — what's the best email to reach you at?"
- ❌ "I have updated your lead profile"

## Never expose internals

Do not send raw profile JSON, field names like `requested_items`, digests, or `sync_result` to the user.
