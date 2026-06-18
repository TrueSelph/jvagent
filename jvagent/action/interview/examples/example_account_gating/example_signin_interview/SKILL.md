---
name: example_signin_interview
description: >-
  Reference PREREQUISITE skill (ADR-0026). Collects an email to establish a
  session. Pushed automatically as a blocking prerequisite when a gated skill's
  `signed_in` precondition is unmet (see example_booking_interview). On its own it
  is just an ordinary task-lock interview — the work graph is what makes it a gate.
spec: jv
task-lock: true
requires-actions:
  - InterviewAction
extends: action:jvagent/interview
tags:
  - example
  - gating
  - reference
interview:
  title: Sign In
  summary: >-
    Establishes a session by collecting the visitor's email. When complete, the
    consumer marks the visitor signed in so the gated skill that pushed this one
    becomes runnable and resumes.
  confirm: auto
  fields:
    - key: email
      prompt: What is your email address?
      required: true
      guidance: A valid email address.
      validator: email
---

# Sign In (prerequisite reference skill)

Pushed as a blocking prerequisite by any skill whose `requires-tasks` names a
precondition this session satisfies. It has no special gating code itself — that
is the point: a prerequisite is just a task-lock skill, and the *graph* (push →
block → drain → resume), not the skill, implements the detour.
