---
name: onboarding_interview
description: Customer onboarding and phone-update interview for Zoon users. Onboards
  new accounts (phone, email, ID, review, create) or updates an existing customer's
  WhatsApp number via OTP. Collected fields are stored on the completed SKILL task
  for reuse (e.g. email on file for phone updates).
spec: jv
task-lock: true
requires-actions:
- InterviewAction
- ZoonAPIAction
extends: action:jvagent/interview
disabled-tools:
- interview__cancel
allowed-tools:
- onboarding_interview__process_id_card
- onboarding_interview__send_otp
tags:
- onboarding
- customer
- interview
- phone-update
interview:
  title: Customer Onboarding
  summary: >-
    Collect customer details (contact number, email, ID card photo or ID/passport
    number, full name, date of birth) for new Zoon account creation, or update an
    existing customer's WhatsApp phone number via OTP.
  confirm: manual
  fields:
  - key: phone_number
    prompt: What is your best phone number?
    required: true
    pre_processor: get_phone_number
    post_processor: verify_phone_number
    guidance: >-
      First onboarding question. On WhatsApp, pre_processor may suggest the number
      on file — ask the user to confirm it. post_processor runs verify_phone_number
      automatically after save.
    validator: phone
    validator_args:
      exact_length: 10
  - key: email
    prompt: What is your email address?
    required: true
    guidance: >-
      User's email address. pre_processor may suggest email from a completed onboarding
      task. post_processor runs verify_email automatically after save.
    pre_processor: suggest_email_from_task
    post_processor: verify_email
    validator: email
  - key: otp_code
    prompt: Please enter the verification code sent to your email.
    required: false
    guidance: >-
      Only ask after onboarding_interview__send_otp succeeded (otp_sent true). If OTP
      was not sent, call interview__skip_field. Validator confirms via API.
    validator: validate_otp_code
  - key: id_card
    prompt: >-
      Do you have a photo of your ID card? Please upload it for faster verification,
      or say 'no' to enter your details manually.
    required: false
    guidance: >-
      Photo of ID card for verification. If uploaded, call process_id_card to extract
      id_number, full_name, and date_of_birth automatically. If the user declines,
      ask for each field manually.
  - key: id_number
    prompt: What is your ID number?
    required: true
    guidance: >-
      National ID number (8 to 9 digits) or passport number. Only ask this if id_card
      was skipped or no photo was uploaded.
    validator: validate_id_number
  - key: full_name
    prompt: What is your full name?
    required: true
    guidance: >-
      User's full name (first and last name). Only ask this if id_card was skipped
      or no photo was uploaded.
    validator: name
  - key: date_of_birth
    prompt: What is your date of birth?
    required: true
    guidance: >-
      Must be a date in the past in DD-MM-YYYY format. Only ask this if id_card was
      skipped or no photo was uploaded.
    validator: date_past
  skill_tools:
  - name: send_otp
    description: >-
      When OTP is required (email mismatch during onboarding, or user is updating
      their phone and email + phone_number are collected). Send verification code to
      the email on the account. Then ask for otp_code if ok, or interview__skip_field
      otp_code if send failed.
    function: send_otp
    parameters: {}
  - name: process_id_card
    description: >-
      When user uploaded an ID photo. Extract and save id_number, full_name, and
      date_of_birth automatically. Then continue the interview or go to review when
      missing_required is empty.
    function: process_id_card
    parameters: {}
  handlers:
    reset: reset_onboarding
    complete: complete_onboarding
---

## Custom instructions

### When to use

- New Zoon user account creation **or** user wants to update/change their WhatsApp phone number.
- Orchestrator may auto-start via `use_skill` on new users.
- Completed fields are saved on the SKILL task (`data.fields`) for reuse (e.g. email on file for phone updates).

### Session overrides

| Situation                                             | Action                                                                                                                                                  |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| After **complete**                                    | Session cleared — call `use_skill` with `onboarding_interview` to start again                                                                    |
| User **cancels** onboarding                           | `interview__reset()` — clears session, cancels tasks, informs user; then **stop** (not `interview__cancel`)               |
| User wants to start over (same as cancel)             | `interview__reset()` — same cancel-and-exit behavior                                                                     |
| `post_tools_results` shows `exists: true`             | **Stop** — follow `response_directive` in one `reply`; persisted interview state is cleared; do not continue onboarding                                 |

### Rules

1. If the user cancels, call **`interview__reset()`** (not `interview__cancel`). Deliver the `response_directive` message and **stop** — do not call `interview__next_field` or ask onboarding questions.
2. **Tools persist data; you do not.** `process_id_card` writes `id_number`, `full_name`, and `date_of_birth` to `fields` automatically. After extraction succeeds, call `interview__review()` when `missing_required` is empty — **never** call `interview__set_fields` for extracted values. Use `set_field` when the user types an answer, or to fix a field at review.
3. **Phone confirm is not a reply-only turn.** When the user confirms the WhatsApp number (`yes`, `ok`, `sure`), call `interview__set_fields` with `{"fields": {"phone_number": "<digits>"}}` — **do not** only acknowledge the number in a reply.
4. **Phone verification runs automatically via `post_tools`** after `phone_number` is saved. Read `post_tools_results` — never call a verify tool manually; do not call `interview__next_field` until `exists: false`.
5. **Already registered = stop.** If `exists: true`, one `reply` per `response_directive` — no `next_field`, email, ID, or `interview__complete`.
6. **Email verification runs automatically via `post_tools`** after `email` is saved. Read `post_tools_results` — never call `verify_email` manually.
7. **OTP only after `send_otp`.** If `otp_pending: true`, call **`onboarding_interview__send_otp`**. Only ask for `otp_code` when `send_otp` returns `otp_sent: true`. If OTP was **not** sent, call **`interview__skip_field("otp_code")`** then continue. Never ask for `otp_code` during normal new-account flow when no OTP was sent.
8. **OTP validation is automatic** in `validate_otp_code` when user submits a code. If `interview_complete: true`, deliver welcome message and **stop** (no review/complete). If invalid, offer resend via `send_otp` and mention the target phone number.
9. **Skipped optional fields** — if `otp_code` was skipped, do not ask for it. Validation does not run on skipped fields.

### Custom tools (`onboarding_interview__*`)

- **`onboarding_interview__send_otp()`**
  - **When:** `verify_email` returned `otp_pending: true`, or update-phone flow after email + phone collected.
  - **Do:** Send verification code to email on the account.
  - **Then:** If `otp_sent: true`, ask for `otp_code`. If failed, `interview__skip_field("otp_code")` and continue (onboard) or retry/stop (update phone).

- **`onboarding_interview__process_id_card()`**
  - **When:** User uploaded an ID photo.
  - **Do:** Extract and save `id_number`, `full_name`, `date_of_birth` from the image.
  - **Then:** Read `ok`, `system_message`, `fields`, and `missing_required`; continue with `interview__next_field` or `interview__review` as appropriate.

### Update phone flow (alternate)

When the user wants to **update/change their WhatsApp number** (not full onboarding): `pre_tools` may suggest email from a prior completed task. If email is on file, confirm and collect only the new `phone_number`; otherwise collect email then phone. Call **`onboarding_interview__send_otp`**. On valid OTP (`interview_complete: true`), deliver the welcome message and **stop** — skip id_card, review, and `interview__complete`.

### ID card handling

Interpret the user's reply to the id_card question (yes/no/paraphrases). If they want photo verification, ask for upload — do **not** `skip_field("id_card")` or ask `id_number` yet. If they decline, `interview__skip_field("id_card")`. On image upload, call **`onboarding_interview__process_id_card()`** once; if extracted and `missing_required` is empty, go to review without `set_field` for extracted values.

On **406 conflict** from `interview__complete`: follow the returned `response_directive` (OTP handling is automatic).

### Tone

Friendly and concise. Bold only the **question text** from `next_fields`. If validation fails, use `error` from the tool and re-ask from `next_fields`.
