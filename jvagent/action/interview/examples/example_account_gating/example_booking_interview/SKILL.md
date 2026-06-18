---
name: example_booking_interview
description: >-
  Reference GATED service skill (ADR-0026). Books an appointment, but only once
  the visitor has an active session. Demonstrates declarative requires-tasks:
  when the `signed_in` precondition is unmet, the harness pushes a sign-in
  prerequisite that blocks this skill and resumes it on completion — no rail, no
  per-field guard, no model-mediated resume. Copy to <action>/skills/ and bind
  the precondition with register_precondition (see ../README.md).
spec: jv
task-lock: true
requires-actions:
  - InterviewAction
extends: action:jvagent/interview
# Declarative account gate (ADR-0026). On activation the harness evaluates these
# in order against the consumer-registered preconditions and pushes the first
# unmet prerequisite as a task that blocks this one, seeding the original request
# so it is preserved across the detour and resumed afterward.
requires-tasks:
  - when: signed_in
    push: example_signin_interview
    seed_from: [utterance]
tags:
  - example
  - gating
  - reference
interview:
  title: Appointment Booking
  summary: >-
    Collects the service type and a preferred date to book an appointment. Gated
    on an active session via requires-tasks; the booking only runs once the
    visitor is signed in.
  confirm: manual
  fields:
    - key: service_type
      prompt: What would you like to book?
      required: true
      guidance: A service name (e.g. "haircut", "consultation"). Not filler.
      validator: text
    - key: preferred_date
      prompt: What date would you like?
      required: true
      guidance: A date or day. Not an acknowledgement.
      validator: text
  completion:
    message: >-
      You're booked for {service_type} on {preferred_date}. See you then!
---

# Appointment Booking (gated reference skill)

This skill is a domain-neutral witness that the work-stack gating in ADR-0026 is
reusable by any consumer — not just the account-gate it was first built for. It
carries no tenant vocabulary; the only gate wiring is the `requires-tasks` block
in the frontmatter plus a single `register_precondition("signed_in", ...)` call
at bootstrap (see `../README.md`).

Flow when a visitor with no session asks to book:

1. The model activates this skill. The harness evaluates `signed_in` → unmet.
2. It pushes `example_signin_interview` as a task that **blocks** this booking
   task, snapshotting + seeding the original request.
3. The sign-in skill runs to completion; the harness drains the work graph and
   **resumes this booking**, re-injecting the original request so the visitor is
   not re-asked for what they already provided.
