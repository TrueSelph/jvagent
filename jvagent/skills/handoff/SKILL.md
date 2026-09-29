---
name: handoff
description: >-
  Reach a human teammate, give the team's contact details, or change the
  phone or email replies are sent to. Use when the user asks for a person,
  wants a callback, or you cannot answer from the knowledge base or catalog.
allowed-tools:
  - handoff__contact_details
  - handoff__update_contact
  - handoff__staff_lookup
  - handoff__agent_escalation
  - handoff__scheduled_callback
requires-actions:
  - HandoffAction
extends: action:jvagent/handoff_action
access-action: HandoffAction
denied-groups:
  - staff
version: 1
tags:
  - handoff
  - human
  - support
  - escalation
  - callback
---

Call `use_skill` for this skill before any of its tools.

## Tools

- `handoff__contact_details` — user wants the team's details only; return contact number and office hours only.
- `handoff__update_contact` — the user wants to change the phone or email replies are sent to. Pass one `phone_number` or one `email`.
- `handoff__staff_lookup` — you cannot answer or the request is outside the assortment. Records a pending question and notifies staff.
- `handoff__agent_escalation` — the user wants a person now.
- `handoff__scheduled_callback` — the user wants to be reached later.

## Channels (edit per agent)

Each tool picks its own channel. Defaults are WhatsApp. Change them on the action if a mode should use email instead. Staff recipients come from AccessControlAction `HandoffAction.staff`. You never choose the channel or the recipient.

## `handoff__staff_lookup` message format

`message` is never shown to the customer. Write it as:

1. **Sentence 1 — natural customer ask only** (this is what gets stored as the pending question). Phrasing must sound natural, e.g. `Customer asked for the company's location.` or `Customer asked if we sell car parts.` — not awkward doubles like "location address".
2. **Optional sentence 2 — what was already tried** (staff notify only; not stored). e.g. `No information found in the FAQ.` or `No information found in the FAQ or catalog.`

Do not put handling notes in sentence 1. Do not mention WhatsApp or tell staff to reply.

## Customer side

1. **Detect intent.** The user explicitly asks for a human / agent / live
   support, wants a callback, or you cannot answer from the knowledge base or
   available tools (an FAQ / knowledge-base search returned nothing relevant, or
   the request is outside what you can do).
2. **Call the matching tool.** For `handoff__staff_lookup`, follow the message
   format above. Pass `phone_numbers` or `emails` only when the user already
   gave one.
3. **Relay the tool's directive** as-is. If it says to ask for a phone or
   email and call the same tool again, do that. If it does not, do not ask
   again and do not call the tool a second time.

If the user only wants the team's details (not a notification), use
`handoff__contact_details` and relay the block exactly as returned.
