---
name: whatsapp_flows
description: >
  Send a WhatsApp Flow (interactive form) to the user who just messaged on
  WhatsApp — e.g. "send me the signup flow", "open the survey form". Only works
  on inbound WhatsApp turns; refuses web/chat.
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

## Hard rules

1. **WhatsApp channel only.** If a tool returns
   `whatsapp_flows_require_inbound_whatsapp`, tell the user to request it on
   WhatsApp — never invent a phone number.
2. **Recipient is fixed** to the inbound sender. Do not ask for another number.
3. Prefer `flow_id` from `whatsapp__list_flows`; `flow_name` is a fallback.

## Procedure

1. If the Flow name/id is unclear, call `whatsapp__list_flows`.
2. Call `whatsapp__send_flow` with `flow_id` (preferred) or `flow_name`, a short
   `body`, and a `flow_cta` (e.g. "Open").
3. On `ok: true`, confirm the Flow was sent. On allowlist/Meta errors, report
   plainly.

Do not confuse with `whatsapp_templates` (HSMs) or `signup_interview` (chat form).
