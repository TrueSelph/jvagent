---
name: signup_interview
description: >-
  Sign up or register for jvagent training. Collect full name, training
  availability slot, email, and optional phone number, then confirm
  registration. Use when the user wants to sign up, register, enroll, or
  join jvagent training.
spec: jv
locked-in: true
requires-actions:
  - InterviewAction
extends: action:jvagent/interview_action
interview:
  title: JVAgent Training Signup
  summary: >-
    Collect full name, training slot, email, and optional phone for jvagent
    training registration. Branching: Saturday slots ask in-person vs virtual;
    @mail.com work emails ask employer name before optional phone.
  confirm: manual
  fields:
    - key: user_name
      prompt: "What's your full name?"
      required: true
      guidance: >-
        A real person's first and last name (e.g. "Jane Doe", "Li Wei"). Not an
        acknowledgement, filler, or conversational reply. If the user says "ok",
        "sure", or repeats a filler word, do not store it — ask again for their name.
      validator: validate_full_name
    - key: available_times
      prompt: "What times are you available to train?"
      required: true
      guidance: >-
        Preferred training slot from the available times list. pre_processor shows
        slots — user must pick one of the listed options.
      pre_processor: get_available_training_times
      validator: validate_available_times
      branches:
        - when: { op: contains, value: Saturday }
          goto: training_format
      else: user_email
    - key: training_format
      prompt: "For your Saturday session, will you attend in person or join virtually?"
      required: true
      guidance: >-
        Only asked when the user picked a Saturday slot. Accept clear answers like
        "in person", "onsite", "virtual", "online", or "remote".
      validator: validate_training_format
    - key: user_email
      prompt: "What is your email?"
      required: true
      guidance: >-
        Valid email for training contact. post_processor may append a note for
        @mail.com work addresses after save.
      post_processor: append_work_email_note
      validator: validate_signup_email
      branches:
        - when: { op: contains, value: "@mail.com" }
          goto: employer_name
      else: phone_number
    - key: employer_name
      prompt: "What company or organization is your @mail.com address associated with?"
      required: true
      guidance: >-
        Only asked for @mail.com work emails. Organization or employer name —
        not a job title alone.
      validator: name
    - key: phone_number
      prompt: "What is your phone number? (optional)"
      required: false
      guidance: >-
        Optional phone contact. Call interview__skip_field when the user
        declines or has nothing to add.
      validator: phone
      validator_args:
        exact_length: 10
  handlers:
    review: signup_review
    complete: signup_complete
tags: [signup, training, interview, onboarding]
---

## Custom instructions

### When to use

- User wants to **sign up**, **register**, **enroll**, or **join jvagent training**.

### Turn flow

**Activation (first turn after `use_skill`)**

- If the user's message includes extractable signup fields (e.g. "Hello my name is Jane Doe"), call `interview__set_fields` with every field you can extract, then `interview__next_question` when `missing_required` is non-empty, then reply.
- Otherwise call `interview__next_question` first, then reply using `next_questions` / `response_directive`.

**Each collection turn**

1. Classify intent (answer, correction, skip optional phone, cancel, start over).
2. Call the matching tool — usually `interview__set_fields` for answers and corrections.
3. Read `ok`, `results`, and `response_directive`. When `missing_required` is non-empty or the directive says `Call interview__next_question`, chain `interview__next_question` before replying.
4. One primary reply per turn unless the directive chains another tool.

**Corrections**

- Name, email, or training slot may be updated at any time via `interview__set_fields` — mid-interview or at review.
- Use `interview__get_fields` or `interview__get_status` if you need current values before updating.
- At **review**: after correcting, call `interview__review()` again, then ask for confirmation before `interview__complete()`.

### Branching

The graph branches on stored field values — unreachable fields are pruned when the user changes an upstream answer.

| After field | Condition | Next field | Skipped |
| ----------- | --------- | ---------- | ------- |
| `available_times` | slot contains **Saturday** | `training_format` → then `user_email` | — |
| `available_times` | weekday slot (default `else`) | `user_email` | `training_format` |
| `user_email` | contains **@mail.com** | `employer_name` → then `phone_number` | — |
| `user_email` | other domains (default `else`) | `phone_number` | `employer_name` |

Use `interview__get_status` / `missing_required` / `next_questions` — do not assume every field in the spec is still on the active path.

### Field-specific rules

- **user_name** — first and last name only; never store acknowledgements or filler ("ok", "sure").
- **available_times** — `interview__next_question` runs `get_available_training_times` pre_processor; present slots in Eastern Time. Partial phrases like "Monday at 9" validate when they match a listed slot.
- **training_format** — only on the Saturday branch; store normalized **In person** or **Virtual**.
- **user_email** — for `@mail.com` addresses, read `post_tools_results` and `response_directive` after store; the work-email thank-you may chain `employer_name` before phone. Follow the directive, then `interview__next_question` when needed.
- **employer_name** — only on the @mail.com branch; company or org name, not a job title alone.
- **phone_number** — optional; `interview__skip_field("phone_number")` when the user declines.

### Rules

- Do not say the user is signed up until after `interview__complete()` succeeds.
- Cancel → `interview__cancel`. Start over → `interview__reset`.
- Do not invent questions — use `interview__next_question` for the active question text.

### Session overrides

| Situation | Action |
| --------- | ------ |
| Optional phone declined | `interview__skip_field("phone_number")`, then `interview__next_question` if needed |
| User corrects a stored field | `interview__set_fields` for that field; at review, re-run `interview__review` |
| After **complete** or **cancel** | Session cleared — call `use_skill` with `signup_interview` to start again |

### Tone

Friendly and concise. Bold only the question text from `next_questions`. On validation failure, use `error` from the tool and re-ask.
