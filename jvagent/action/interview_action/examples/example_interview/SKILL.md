---
name: example_interview
description: 'Reference product feedback interview. Collects customer name, product
  rating, optional comments, and follow-up email. Demonstrates all InterviewAction
  patterns: validators, pre_tools, post_tools, custom tools, review, and completion.
  Copy to <action>/skills/<your_skill_name>/ to create a live skill.'
spec: jv
locked-in: true
requires-actions:
- InterviewAction
extends: action:jvagent/interview_action
allowed-tools:
- interview__set_field
- interview__get_field
- interview__skip_field
- interview__next_question
- interview__get_status
- interview__review
- interview__complete
- interview__cancel
- example_interview__reset_example_interview
tags:
- example
- feedback
- interview
- reference
interview:
  title: Product Feedback
  description: 'Reference interview skill that collects product feedback (name, rating,
    optional comments, follow-up email). Demonstrates all InterviewAction frontmatter
    interview: features: builtin and custom validators, pre_tools, post_tools, LLM-callable custom
    tools, custom review, and completion handlers. Copy this folder to skills/<your_skill_name>/
    to create a live skill under the action skills/ folder. The LLM decides which question to ask next based on SKILL.md.'
  questions:
  - name: customer_name
    question: What is your name?
    required: true
    description: Customer's full name for the feedback record.
    validator:
      function: name
  - name: product_rating
    question: On a scale of 1 to 5, how would you rate the product?
    required: true
    description: Integer rating from 1 (poor) to 5 (excellent). post_tools runs check_low_rating
      automatically after save — read post_tools_results before advancing.
    post_tools:
    - check_low_rating
    validator:
      function: validate_rating
  - name: feedback_comments
    question: Would you like to share any additional comments? (You can skip this)
    required: false
    description: Optional free-text feedback. Call interview__skip_field if the user
      declines.
    validator:
      function: description
      kwargs:
        min_length: 3
        max_length: 500
  - name: follow_up_email
    question: What email address should we use for follow-up?
    required: true
    description: Email for follow-up. pre_tools may suggest an address from conversation
      context — ask the user to confirm before saving.
    pre_tools:
    - suggest_email
    validator:
      function: email
  tools:
  - name: reset_example_interview
    description: 'When: User cancels the interview or wants to start over. Do: Clear
      the session and restart from the first question. Then: Call interview__next_question.
      Use this instead of interview__cancel when the user abandons and may return.'
    function: reset_example_interview
    parameters: {}
  review:
    function: example_review
    description: Escalation path when product_rating is low (terminate without complete),
      or formatted summary for user confirmation before completion.
  completion:
    function: example_complete
    description: Post-review completion handler called by interview__complete. Stores
      feedback in session context and returns a confirmation message.
---

> **Note:** Reference package under `interview_action/examples/` (not auto-discovered). Copy to your app overlay at `agents/<ns>/<agent>/actions/jvagent/interview_action/skills/<name>/` and register in `agent.yaml` to activate.

## Custom instructions

### When to use

- Product feedback collection — rating, comments, follow-up email.
- Reference template demonstrating InterviewAction patterns (validators, pre/post tools, review, completion).

### Session overrides

| Situation                                             | Action                                                                                                      |
| ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| After **complete**                                    | Session cleared — call `use_skill` with `example_interview` to start again                                  |
| User **cancels** or wants to start over               | `example_interview__reset_example_interview()` — clears session and re-inits; or `interview__cancel()`      |
| `post_tools_results` shows `skip_to_review: true`     | Call `interview__review()` — do not ask remaining questions                                                 |
| Review sets `terminate: true`                         | Deliver escalation message — do **not** call `interview__complete()`                                        |

### Rules

1. **Low-rating check runs automatically via `post_tools`** after `product_rating` is saved. Read `post_tools_results` — never call `check_low_rating` manually.
2. **Email suggestion is not a reply-only turn.** When pre_tools suggests an email and the user confirms, call `interview__set_field(field="follow_up_email", value=<email>)`.
3. **Optional comments must be offered.** Ask `feedback_comments` or call `interview__skip_field("feedback_comments")` when the user declines — do not call `interview__review` until `next_questions` is empty.
4. **Escalation path skips complete.** When review returns `terminate: true`, deliver the directive message and stop — no `interview__complete()`.

### Tone

Friendly and concise. Bold only the **question text** from `next_questions`. If validation fails, use `error` from the tool and re-ask from `next_questions`.
