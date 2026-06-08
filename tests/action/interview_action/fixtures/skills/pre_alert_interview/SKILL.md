---
name: pre_alert_interview
description: Tracking and pre-alert interview for Zoon users. Checks shipment/pre-alert
  status or collects details to create a new pre-alert. Use for ANY request involving
  a tracking number, package tracking, shipment status, or pre-alert creation.
spec: jv
locked-in: true
requires-actions:
- InterviewAction
- ZoonAPIAction
extends: action:jvagent/interview_action
tags:
- pre-alert
- shipment
- tracking
- status
- order
interview:
  title: Tracking & Pre-Alert
  description: 'Handles tracking number lookups: checks shipment/pre-alert status
    and creates new pre-alerts. When the user provides a tracking number, checks conversation
    context for an existing pre-alert. If found or a known status number, returns
    status at review. Otherwise collects description, invoice value, and alternative
    tracking number to create a new pre-alert via the Zoon API. The LLM decides which
    question to ask next based on SKILL.md.'
  questions:
  - name: tracking_number
    question: What is the tracking number for your package?
    required: true
    description: User's tracking number for the shipment
    post_tools:
    - check_tracking_status
    validator:
      function: validate_tracking_number
      kwargs:
        min_length: 10
  - name: description
    question: What is the description of the item(s) you're shipping?
    required: true
    description: Description of the shipped item(s)
    validator:
      function: description
      kwargs:
        min_length: 10
        max_length: 500
  - name: invoice_value
    question: What is the invoice value of the item? (You can skip this if you don't
      know)
    required: false
    description: Invoice or declared value (optional)
    validator:
      function: validate_invoice_value
  - name: alternative_tracking_number
    question: Do you have an alternative tracking number? (You can skip this if you
      don't have one)
    required: false
    description: Alternative tracking number (optional)
    validator:
      function: validate_alternative_tracking_number
  review:
    function: pre_alert_review
    description: Status-only path when tracking_status is in session context (terminate),
      or formatted summary for user confirmation before completion.
  completion:
    function: pre_alert_complete
    description: Creates the pre-alert via Zoon API after user confirms at review.
      Updates user_pre_alerts in conversation context on success.
---

## Custom instructions

### When to use

- ANY request involving a tracking number, package tracking, shipment status, or pre-alert creation.
- Call `use_skill` with `pre_alert_interview` — may seed a tracking number from the user's latest message.

### Session overrides

| Situation        | Action                                                                                       |
| ---------------- | -------------------------------------------------------------------------------------------- |
| User **cancels** | `interview__cancel()` (clears session). To start again, call `use_skill` with `pre_alert_interview`. |

### Rules

1. **After description, ask optional fields before review.** While `next_questions` lists `invoice_value` or `alternative_tracking_number`, ask them (or `interview__skip_field` when the user declines) — do not call `interview__review` until `next_questions` is empty.
2. **Tracking status check runs automatically via `post_tools`** when `tracking_number` is stored. Read `post_tools_results`; if `skip_to_review: true`, call `interview__review()`; else continue per `next_tool` / `response_directive`. Never call a tracking-status tool manually.
3. For tracking requests **without** a tracking number in the latest message: ask for the **tracking number** only. Never ask for email, ID, or date of birth in this skill.
4. Do not invent extra questions (weight, dimensions, origin, destination, etc.).
5. Call `interview__review` when **`next_questions` is empty** (optional fields set or skipped) — do not use `missing_required` alone as the signal.

### Tone

Friendly and concise. Bold only the **question text** from `next_questions`. If validation fails, use `error` from the tool and re-ask from `next_questions`.
