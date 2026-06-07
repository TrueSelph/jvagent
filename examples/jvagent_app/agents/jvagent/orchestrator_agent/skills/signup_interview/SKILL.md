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
allowed-tools:
  - interview__set_field
  - interview__get_field
  - interview__skip_field
  - interview__next_question
  - interview__get_status
  - interview__review
  - interview__complete
  - interview__cancel
  - signup_interview__reset_signup_interview
interview:
  title: JVAgent Training Signup
  description: >-
    Collect full name, training availability slot, email, and optional phone
    for jvagent training registration. Use when the user wants to sign up,
    register, enroll, or join jvagent training.
  questions:
    - name: user_name
      question: "What's your full name?"
      required: true
      description: >-
        User's full name — must include first and last name (at least two words,
        letters/spaces/hyphens/apostrophes only).
      validator:
        function: validate_full_name
    - name: available_times
      question: "What times are you available to train?"
      required: true
      description: >-
        Preferred training slot from the available times list. pre_tools shows
        slots — user must pick one of the listed options.
      pre_tools:
        - get_available_training_times
      validator:
        function: validate_available_times
    - name: user_email
      question: "What is your email?"
      required: true
      description: >-
        Valid email for training contact. post_tools may append a note for
        @mail.com work addresses after save.
      post_tools:
        - append_work_email_note
      validator:
        function: validate_signup_email
    - name: phone_number
      question: "What is your phone number? (optional)"
      required: false
      description: >-
        Optional phone contact. Call interview__skip_field when the user
        declines or has nothing to add.
      validator:
        function: phone
        kwargs:
          exact_length: 10
  tools:
    - name: reset_signup_interview
      description: >-
        When: User cancels signup or wants to start over. Do: Clear the session
        and restart from the first question. Then: Call interview__next_question.
        Use instead of interview__cancel when the user may return later.
      function: reset_signup_interview
      parameters: {}
  review:
    function: signup_review
    description: >-
      Summary for user confirmation before completion. Omits empty optional
      phone from the display.
  completion:
    function: signup_complete
    description: >-
      Post-review handler called by interview__complete. Records signup and
      returns a confirmation message.
tags: [signup, training, interview, onboarding]
---

## Custom instructions

### When to use

- User wants to **sign up**, **register**, **enroll**, or **join jvagent training**.

### Rules

- For `@mail.com` emails, read `post_tools_results` before advancing — the post_tool may deliver a work-email thank-you **and** the next question in one `response_directive`; do not stop after the thank-you only.
- Available training slots are shown via `pre_tools` on `available_times` — present the list in Eastern Time. Partial answers like "Monday at 9" are autocorrected when they match a slot.
- During review, registration is NOT complete until the user confirms — do not say the user is signed up until after `interview__complete()`.

### Session overrides

| Situation | Action |
| --------- | ------ |
| User **cancels** or wants to start over | `signup_interview__reset_signup_interview()` or `interview__cancel()` |
| Optional phone declined | `interview__skip_field("phone_number")` then continue |
| After **complete** | Session cleared — call `use_skill` with `signup_interview` to start again |

### Tone

Friendly and concise. Bold only the question text from `next_questions`. On validation failure, use `error` from the tool and re-ask.
