---
name: handoff
description: >-
  Reach a human teammate, give the team's phone, email, or office hours on
  request, or change the phone or email replies are sent to. Use when the user
  asks for a person, wants a callback, or you cannot answer from the knowledge
  base or catalog. A question the knowledge base cannot answer is never a
  request for contact details.
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

- `handoff__contact_details` — use only when the user explicitly requests a contact channel (phone or email) or office hours. It returns no business information; any question the knowledge base cannot answer is not a contact-details request and goes to `handoff__staff_lookup`.
- `handoff__update_contact` — the user wants to change the phone or email replies are sent to. Pass one `phone_number` or one `email`.
- `handoff__staff_lookup` — owns every request the knowledge base cannot answer: anything outside the assortment or any business fact no document provides. Records a pending question and notifies staff.
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

Use `handoff__contact_details` only for an explicit request for a contact
channel (phone or email) or office hours. Any other question the knowledge base
cannot answer is not a contact-details request; use `handoff__staff_lookup`.
Relay the tool's returned block exactly as returned.
