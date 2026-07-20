---
name: whatsapp_service_flows
description: >
  Send the published signup/signin or appointment-booking WhatsApp Flow when the
  user asks to sign up, sign in, log in, create an account, book an appointment,
  or schedule a visit. Works on WhatsApp text and WhatsApp voice-call turns;
  refuses web/chat. Prefer this over web search for those intents.
spec: jv
always-active: true
requires-actions:
  - WhatsAppAction
allowed-tools:
  - whatsapp__send_flow
tags:
  - whatsapp
  - flows
  - signup
  - signin
  - appointment
---

# WhatsApp Service Flows — Standard Operating Procedure

Use this when the user wants to **sign up / sign in** or **book an appointment**
via the published Meta Flows — not the multi-turn `signup_interview` chat form,
not a message template HSM, and not an arbitrary Flow (use `whatsapp_flows` for
that).

## Hard rules

1. **WhatsApp text or WhatsApp voice-call only.** If a tool returns
   `whatsapp_flows_require_inbound_whatsapp`, tell the user to request it on
   WhatsApp (chat or call) — never invent a phone number.
2. **Recipient is fixed** to the inbound sender / caller (`user_id`). Do not ask
   for another number.
3. **Map intent to a fixed Flow** — do not call `whatsapp__list_flows` for these
   product Flows; use the `flow_name` values below.
4. Pass **`flow_name` only** — do not also pass `flow_id`. Meta accepts one or
   the other; sending both can fail with a parameter error.

## Intent → Flow map

| User intent (examples) | `flow_name` | Suggested `flow_cta` | Suggested `body` |
|---|---|---|---|
| signup, sign up, signin, sign in, log in, create account, register | `signin_signup` | Continue | Please complete sign-in or sign-up. |
| book appointment, schedule appointment, schedule a visit, appointment booking | `appointment_booking` | Book | Please choose a time for your appointment. |

Prefer navigate / No data (omit `flow_action`, or use `navigate` when a screen id
is known). Do not use `data_exchange` unless you know the Flow needs endpoint INIT.

## Verbiage (user-facing)

Always call these "forms" when talking to the user — "sign-up form",
"sign-in form", "appointment booking form". Never say "flow" to the user;
"Flow" is Meta's internal name, not something users recognize.

## Procedure

1. Classify the utterance into one of the two intents above.
2. IMMEDIATELY call `whatsapp__send_flow` with that row's `flow_name`, `body`,
   and `flow_cta` (omit `flow_id`). Do NOT announce that you "will send" the
   form first — announcing without calling the tool ends the turn and nothing
   is sent. The tool call IS the send.
3. Only after the tool returns `ok: true`:
   - **WhatsApp text:** briefly confirm (e.g. "I've sent you the sign-up form —
     tap the button above to continue.").
   - **WhatsApp voice call:** speak a short line such as "I sent the form to
     this WhatsApp chat; please open it there." Spoken replies stay on the
     call; the form card appears in the WhatsApp chat.
4. On allowlist / Meta / channel errors, report plainly. Do not invent a phone
   number or fall back to `signup_interview` unless the user explicitly asks for
   a chat interview instead.

## Handling a submitted form

When the user completes the form, the submission arrives as the next user
message containing raw JSON (field keys/values plus a `flow_token`, e.g.
`{"first_name": "...", "email": "...", "flow_token": "..."}`).

1. Treat it as the user's completed form — NOT as a request to send a form.
   Do not call `whatsapp__send_flow` again.
2. Reply naturally confirming what was completed, e.g. "Thanks Jane, your
   sign-up is complete — we'll follow up at jane@example.com." Reference only
   non-sensitive fields; NEVER echo passwords or confirm_password values.
3. If useful, store durable facts (name, email) in user memory.

Do not confuse with `whatsapp_templates` (HSMs), `whatsapp_flows` (generic form
send), or `signup_interview` (multi-turn chat form).
