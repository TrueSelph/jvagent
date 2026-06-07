---
name: onboarding_interview
description: Customer onboarding and phone-update interview for Zoon users. Onboards
  new accounts (phone, email, ID, review, create) or updates an existing customer's
  WhatsApp number via OTP. Collected fields are stored on the completed SKILL task
  for reuse (e.g. email on file for phone updates).
spec: jv
locked-in: true
requires-actions:
- InterviewAction
- ZoonAPIAction
allowed-tools:
- interview__set_field
- interview__get_field
- interview__skip_field
- interview__next_question
- interview__get_status
- interview__review
- interview__complete
- onboarding_interview__reset_onboarding
- onboarding_interview__process_id_card
- onboarding_interview__send_otp
tags:
- onboarding
- customer
- interview
- phone-update
interview:
  title: Customer Onboarding
  description: Collect customer details (contact number, email, ID card photo or ID/passport
    number, full name, date of birth) for new Zoon account creation, or update an
    existing customer's WhatsApp phone number via OTP. The LLM decides which question
    to ask next based on SKILL.md.
  questions:
  - name: phone_number
    question: What is your best phone number?
    required: true
    pre_tools:
    - get_phone_number
    post_tools:
    - verify_phone_number
    description: First onboarding question. On WhatsApp, pre_tools may suggest the
      number on file — ask the user to confirm it. post_tools runs verify_phone_number
      automatically after save.
    validator:
      function: phone
      kwargs:
        exact_length: 10
  - name: email
    question: What is your email address?
    required: true
    description: User's email address. pre_tools may suggest email from a completed
      onboarding task. post_tools runs verify_email automatically after save.
    pre_tools:
    - suggest_email_from_task
    post_tools:
    - verify_email
    validator:
      function: email
  - name: otp_code
    question: Please enter the verification code sent to your email.
    required: false
    description: Only ask after onboarding_interview__send_otp succeeded (otp_sent
      true). If OTP was not sent, call interview__skip_field. Validator confirms via
      API.
    validator:
      function: validate_otp_code
  - name: id_card
    question: Do you have a photo of your ID card? Please upload it for faster verification,
      or say 'no' to enter your details manually.
    required: false
    description: Photo of ID card for verification. If uploaded, call process_id_card
      to extract id_number, full_name, and date_of_birth automatically. If the user
      declines, ask for each field manually.
  - name: id_number
    question: What is your ID number?
    required: true
    description: National ID number (8 to 9 digits) or passport number. Only ask this
      if id_card was skipped or no photo was uploaded.
    validator:
      function: validate_id_number
  - name: full_name
    question: What is your full name?
    required: true
    description: User's full name (first and last name). Only ask this if id_card
      was skipped or no photo was uploaded.
    validator:
      function: name
  - name: date_of_birth
    question: What is your date of birth?
    required: true
    description: Must be a date in the past in DD-MM-YYYY format. Only ask this if
      id_card was skipped or no photo was uploaded.
    validator:
      function: date_past
  tools:
  - name: send_otp
    description: 'When: OTP is required (email mismatch during onboarding, or user
      is updating their phone and email + phone_number are collected). Do: Send verification
      code to the email on the account. Then: Ask for otp_code if ok, or interview__skip_field
      otp_code if send failed.'
    function: send_otp
    parameters: {}
  - name: process_id_card
    description: 'When: User uploaded an ID photo, or you need to check whether one
      is present. Do: Extract and save id_number, full_name, and date_of_birth automatically.
      Then: continue the interview or go to review when missing_required is empty.'
    function: process_id_card
    parameters: {}
  - name: reset_onboarding
    description: 'When: User cancels onboarding or wants to start over. Do: Clear
      the session, cancel tasks, and inform the user onboarding was cancelled. Then:
      Tell the user they must complete onboarding to chat with the agent and stop
      — do not ask questions or call interview__next_question. To restart later, user
      re-initiates onboarding via use_skill. Call this instead of interview__cancel
      when the user abandons onboarding.'
    function: reset_onboarding
    parameters: {}
  completion:
    function: complete_onboarding
    description: Post-review completion handler called by interview__complete. Creates
      the customer account via Zoon API and marks onboarding complete on success.
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
| User **cancels** onboarding                           | `onboarding_interview__reset_onboarding()` — clears session, cancels tasks, informs user; then **stop** (not `interview__cancel`)               |
| User wants to start over (same as cancel)             | `onboarding_interview__reset_onboarding()` — same cancel-and-exit behavior                                                                     |
| `post_tools_results` shows `exists: true`             | **Stop** — follow `response_directive` in one `reply`; persisted interview state is cleared; do not continue onboarding                                 |

### Rules

1. If the user cancels, call **`onboarding_interview__reset_onboarding()`** (not `interview__cancel`). Deliver the `response_directive` message and **stop** — do not call `interview__next_question` or ask onboarding questions.
2. **Tools persist data; you do not.** `process_id_card` writes `id_number`, `full_name`, and `date_of_birth` to `fields` automatically. After extraction succeeds, call `interview__review()` when `missing_required` is empty — **never** call `interview__set_field` for extracted values. Use `set_field` when the user types an answer, or to fix a field at review.
3. **Phone confirm is not a reply-only turn.** When the user confirms the WhatsApp number (`yes`, `ok`, `sure`), call `interview__set_field(field="phone_number", value=<digits>)` — **do not** only acknowledge the number in a reply.
4. **Phone verification runs automatically via `post_tools`** after `phone_number` is saved. Read `post_tools_results` — never call a verify tool manually; do not call `interview__next_question` until `exists: false`.
5. **Already registered = stop.** If `exists: true`, one `reply` per `response_directive` — no `next_question`, email, ID, or `interview__complete`.
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
  - **Then:** Read `ok`, `system_message`, `fields`, and `missing_required`; continue with `interview__next_question` or `interview__review` as appropriate.

- **`onboarding_interview__reset_onboarding()`**
  - **When:** User cancels onboarding or wants to start over.
  - **Do:** Clear session, cancel tasks, inform user onboarding was cancelled.
  - **Then:** Tell user they must complete onboarding to chat with the agent. **Stop** — do not call `interview__next_question` or ask for phone/email/ID.

### Update phone flow (alternate)

When the user wants to **update/change their WhatsApp number** (not full onboarding): `pre_tools` may suggest email from a prior completed task. If email is on file, confirm and collect only the new `phone_number`; otherwise collect email then phone. Call **`onboarding_interview__send_otp`**. On valid OTP (`interview_complete: true`), deliver the welcome message and **stop** — skip id_card, review, and `interview__complete`.

### ID card handling

Interpret the user's reply to the id_card question (yes/no/paraphrases). If they want photo verification, ask for upload — do **not** `skip_field("id_card")` or ask `id_number` yet. If they decline, `interview__skip_field("id_card")`. On image upload, call **`onboarding_interview__process_id_card()`** once; if extracted and `missing_required` is empty, go to review without `set_field` for extracted values.

On **406 conflict** from `interview__complete`: follow the returned `response_directive` (OTP handling is automatic).

### Tone

Friendly and concise. Bold only the **question text** from `next_questions`. If validation fails, use `error` from the tool and re-ask from `next_questions`.
