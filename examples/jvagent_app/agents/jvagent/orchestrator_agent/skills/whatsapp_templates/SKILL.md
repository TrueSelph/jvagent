---
name: whatsapp_templates
description: >
  Send an approved WhatsApp Meta message template (HSM) to the user who just
  messaged on WhatsApp — e.g. "send me the signup template", "send the welcome
  template". Only works on inbound WhatsApp turns; refuses web/chat.
spec: jv
requires-actions:
  - WhatsAppAction
allowed-tools:
  - whatsapp__list_templates
  - whatsapp__send_template
tags:
  - whatsapp
  - templates
---

# WhatsApp Templates — Standard Operating Procedure

Use this when the user asks to send a named WhatsApp **message template**
(approved Meta HSM), not when they want a multi-turn signup interview or a
Word/PDF document.

## Hard rules

1. **WhatsApp channel only.** Call tools only on this inbound WhatsApp turn.
   If a tool returns `whatsapp_templates_require_inbound_whatsapp`, tell the user
   they must request the template via WhatsApp — do not invent a phone number.
2. **Recipient is fixed.** The tools always send to the same WhatsApp number
   that messaged this turn. Never ask for or invent a different phone.
3. Use the **exact template name** the user asked for (normalized to Meta's
   name, e.g. `signup`). If unsure which names exist, call
   `whatsapp__list_templates` first.

## Procedure

1. If the user named a template (e.g. "send me the signup template"), call
   `whatsapp__send_template` with that `template_name`.
2. If they ask what templates exist, or the name is ambiguous, call
   `whatsapp__list_templates`, then send the matching one if they pick.
3. On `ok: true`, confirm briefly that the template was sent (e.g. "Sent the
   signup template.").
4. On allowlist or Meta errors, report the failure plainly; do not claim the
   message was delivered.

Do not activate `signup_interview` for "send the signup template" — that skill
is a multi-turn registration form, not a Meta HSM.
