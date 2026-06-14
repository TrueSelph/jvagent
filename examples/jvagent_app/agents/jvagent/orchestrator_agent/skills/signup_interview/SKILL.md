---
name: signup_interview
description: >-
  Sign up or register for jvagent training. Collect full name, training
  availability slot, email, and optional phone number, then confirm
  registration. Use when the user wants to sign up, register, enroll, or
  join jvagent training.
spec: jv
requires-actions:
  - InterviewAction
extends: action:jvagent/interview
# Permit a web-search side question mid-signup without dropping the turn-lock:
# the agent answers, then returns to the active interview step.
lock-companions:
  - web_search__search
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

### Tone
Relay the questions and hints in a  friendly and helpful manner.
