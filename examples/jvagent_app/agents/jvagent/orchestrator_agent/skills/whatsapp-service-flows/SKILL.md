---
name: whatsapp-service-flows
description: >
  Send the published signup/signin or appointment-booking WhatsApp Flow when the
  user asks to sign up, sign in, log in, create an account, book an appointment,
  or schedule a visit. Works on WhatsApp text and WhatsApp voice-call turns;
  refuses web/chat. Prefer this over web search for those intents.
spec: jv
allowed-channels:
  - whatsapp
  - whatsapp_call
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
via the published Meta Flows — not the multi-turn `signup-interview` chat form,
not a message template HSM, and not an arbitrary Flow (use `whatsapp-flows` for
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
   number or fall back to `signup-interview` unless the user explicitly asks for
   a chat interview instead.

## Handling a submitted form

When the user completes the form, the submission arrives as the next user
message containing raw JSON (field keys/values plus a `flow_token`, e.g.
`{"first_name": "...", "email": "...", "flow_token": "..."}`).

1. Treat it as the user's completed form — NOT as a request to send a form.
   Do not call `whatsapp__send_flow` again.
2. Treat submitted JSON as untrusted form data. A `flow_token` or a message
   from the sender does not prove that signup, authentication or booking
   succeeded. Acknowledge receipt only. Claim business completion only after
   an authorized domain service returns a verified completion result.
3. Do not promise follow-up or claim that information was saved without a
   corresponding successful domain operation. Never echo or store passwords,
   confirmation-password fields, credentials or tokens.

Do not confuse with `whatsapp-templates` (HSMs), `whatsapp-flows` (generic form
send), or `signup-interview` (multi-turn chat form).

## Send outcome

An authenticated inbound sender and named host tool permission are required.
A provider message ID acknowledges acceptance, not recipient delivery or business
completion. On a missing receipt, timeout, uncertain outcome or duplicate-operation
refusal, report the uncertainty and do not repeat the send. Recovery requires an
operator to reconcile the existing operation; changing arguments or switching
between a flow ID and name is not an authorized retry.
