---
name: whatsapp_flows
description: >
  Send a WhatsApp Flow (interactive form) to the user on a WhatsApp text or
  WhatsApp voice-call turn — e.g. "send me the signup flow", "open the survey
  form". Refuses web/chat. For signup/signin or appointment booking intents,
  prefer whatsapp_service_flows.
spec: jv
requires-actions:
  - WhatsAppAction
allowed-tools:
  - whatsapp__list_flows
  - whatsapp__send_flow
tags:
  - whatsapp
  - flows
---

# WhatsApp Flows — Standard Operating Procedure

Use this when the user asks to open/send a WhatsApp **Flow** (Meta interactive
form), not a message template HSM and not the multi-turn `signup_interview`.

For natural-language **signup / signin** or **appointment booking** requests,
prefer the `whatsapp_service_flows` skill (fixed published Flow ids).

## Hard rules

1. **WhatsApp text or WhatsApp voice-call only.** If a tool returns
   `whatsapp_flows_require_inbound_whatsapp`, tell the user to request it on
   WhatsApp — never invent a phone number.
2. **Recipient is fixed** to the inbound sender / caller. Do not ask for another
   number.
3. Prefer `flow_name` from `whatsapp__list_flows` (pass name only — do not also
   pass `flow_id`; Meta accepts one or the other).
4. Only send Flows returned by `whatsapp__list_flows` (already filtered by the
   agent `flow_allowlist`).
5. On a **WhatsApp voice call**, after a successful send, tell the user the form
   was sent to this WhatsApp chat so they can open it there (spoken reply stays
   on the call; the Flow card appears in chat).
6. **User-facing verbiage:** call these "forms" when talking to the user (e.g.
   "survey form"), never "flows".

## Procedure

1. If the Flow name is unclear, call `whatsapp__list_flows`.
2. Call `whatsapp__send_flow` with `flow_name` only, a short `body`, and a
   `flow_cta` (e.g. "Open"). Prefer navigate / No data Flows (omit
   `flow_action` or use `navigate` plus `screen` when known).
3. **Prefill known fields:** when user memory or the conversation already has
   values that match this Flow’s published field keys, pass them as
   `screen_data` together with the correct `screen` id (e.g.
   `screen="PROFILE_SCREEN"`, `screen_data={"name": "<known name>"}`). Only
   include keys the Flow JSON binds; do not invent screen ids or field names.
   Prefill requires navigate — never combine `screen_data` with
   `data_exchange`.
4. On `ok: true`, confirm the Flow was sent. On allowlist/Meta errors, report
   plainly.

Do not confuse with `whatsapp_templates` (HSMs), `whatsapp_service_flows`
(signup/signin + appointment intents), or `signup_interview` (chat form).
