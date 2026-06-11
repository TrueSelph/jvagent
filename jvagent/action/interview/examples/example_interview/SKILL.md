---
name: example_interview
description: 'Reference product feedback interview. Collects customer name, product
  rating, optional comments, and follow-up email. Demonstrates all InterviewAction
  patterns: validators, pre_processor, post_processor, custom tools, review, and completion.
  Copy to <action>/skills/<your_skill_name>/ to create a live skill.'
spec: jv
locked-in: true
requires-actions:
- InterviewAction
extends: action:jvagent/interview
allowed-tools:
  - example_interview__send_followup_reminder
tags:
- example
- feedback
- interview
- reference
interview:
  title: Product Feedback
  summary: >-
    Reference interview skill that collects product feedback (name, rating,
    optional comments, follow-up email). Demonstrates InterviewAction frontmatter:
    validators, pre_processor, post_processor, LLM-callable skill_tools, custom
    review, and completion handlers. Copy this folder to skills/<your_skill_name>/
    to create a live skill under the action skills/ folder.
  confirm: manual
  fields:
  - key: customer_name
    prompt: What is your name?
    required: true
    guidance: Customer's full name for the feedback record.
    validator: name
  - key: product_rating
    prompt: On a scale of 1 to 5, how would you rate the product?
    required: true
    guidance: >-
      Integer rating from 1 (poor) to 5 (excellent). post_processor runs
      check_low_rating automatically after save — read post_tools_results before advancing.
    post_processor: check_low_rating
    validator: validate_rating
  - key: feedback_comments
    prompt: Would you like to share any additional comments? (You can skip this)
    required: false
    guidance: Optional free-text feedback. Call interview__skip_field if the user declines.
    validator: description
    validator_args:
      min_length: 3
      max_length: 500
  - key: follow_up_email
    prompt: What email address should we use for follow-up?
    required: true
    guidance: >-
      Email for follow-up. pre_processor may suggest an address from conversation
      context — ask the user to confirm before saving.
    pre_processor: suggest_email
    validator: email
  handlers:
    review: example_review
    complete: example_complete
    reset: reset_example_interview
  skill_tools:
    - name: send_followup_reminder
      function: send_followup_reminder
      description: >-
        Optional LLM-callable tool after follow_up_email is saved — records that a
        follow-up reminder was queued (demo stub).
---

> **Note:** Reference package under `interview/examples/` (not auto-discovered). Copy to `agents/<ns>/<agent>/skills/<name>/` and register in `agent.yaml` to activate.

## Custom instructions

### When to use

- Product feedback collection — rating, comments, follow-up email.
- Reference template demonstrating InterviewAction patterns (validators, pre/post processors, review, completion).

### Session overrides

| Situation                                             | Action                                                                                                      |
| ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| After **complete**                                    | Session cleared — call `use_skill` with `example_interview` to start again                                  |
| `post_tools_results` shows `next_tool: interview__review` | Call `interview__review()` — do not ask remaining questions                                             |
| Review sets `terminate: true`                         | Deliver escalation message — do **not** call `interview__complete()`                                        |

### Rules

1. **Low-rating check runs automatically via `post_processor`** after `product_rating` is saved. Read `post_tools_results` — never call `check_low_rating` manually.
2. **Email suggestion is not a reply-only turn.** When pre_processor suggests an email and the user confirms, call `interview__set_fields` with `{"fields": {"follow_up_email": "<email>"}}`.
3. Call `interview__review` when `missing_required` is empty, then `interview__complete` after user confirms.

### Tone

Friendly and concise. Bold only the **question text** from `next_fields`.
