# Interview Skill — Custom Instructions (template)

The **standard interview procedure** is composed via `extends: action:jvagent/interview_action` at skill discovery. Authors write **custom instructions only** in the per-skill `SKILL.md` body — do not copy core procedure steps or enumerate fields.

Canonical base SOP: [`../SKILL.md`](../SKILL.md).

## What to put in the body

| Include | Do not include |
| ------- | -------------- |
| When to use (1–3 bullets) | Flow overview listing each field |
| Domain-specific session overrides | Procedure Step 1…N per field |
| Behavioral rules (OTP gates, exists:true stop) | Duplicated core instructions |
| Custom tool callouts when non-obvious | Question wording (use `interview.questions[].description` + `next_questions`) |
| | **Answer quality gate** (base procedure) |
| | **Message evaluation** — `interview__message_evaluation` / `interview__next_question` prep (base procedure) |
| | **Intent routing** — cancel vs start over vs answer (base procedure) |
| | Cancel/reset/`set_field` decision rules (base procedure) |
| | Chaining rules and `Tell the user:` reply-only turns (base procedure) |
| | Per-field "when X appears in applicable, call set_field" restatements (base covers first missing applicable field) |

## Question descriptions as acceptance criteria

Each `interview.questions[].description` is **model-facing acceptance criteria** — what counts as a substantive answer for that field. It is surfaced in `next_questions` tool observations alongside the question text.

Write descriptions to help the model apply the base procedure's **Answer quality gate** (see [`../SKILL.md`](../SKILL.md)):

- State what a valid answer looks like (with 1–2 examples).
- State what to reject in natural language (acknowledgements, filler, off-topic replies).
- For optional fields, note when to use `interview__skip_field` instead of `set_field`.

Example:

```yaml
- name: user_name
  question: "What's your full name?"
  description: >-
    A real first and last name (e.g. "Jane Doe"). Not an acknowledgement or
    filler — if the user says "ok" or "sure", reply only and re-ask.
```

## Minimal template

```markdown
---
name: my_interview
description: >-
  What this interview does and when to use it (third person).
requires-actions:
  - InterviewAction
extends: action:jvagent/interview_action
# Optional: add LLM-callable custom tools (merged with base allowed-tools)
allowed-tools:
  - my_interview__send_otp
interview:
  title: My Interview
  # Optional: override base reset (same pattern as review/completion)
  reset:
    function: reset_my_interview
    description: Custom start-over or cancel-and-exit behavior.
  questions: [...]
---
```

### `allowed-tools` and `disabled-tools` (extends merge)

When a skill declares `extends: action:jvagent/interview_action`, the base action's `allowed-tools` frontmatter is merged at discovery:

- **`allowed-tools` on the skill** — **additive only**. List custom LLM tools (e.g. `{skill}__send_otp`). Base `interview__*` tools are inherited automatically — do not re-list them.
- **`disabled-tools`** — Remove specific base tools from the merged set. Use when the skill must not expose a base tool (e.g. `interview__cancel` when cancel is handled via `interview.reset` + `interview__reset_interview`).

Example: onboarding-style cancel-and-exit via reset handler:

```yaml
extends: action:jvagent/interview_action
disabled-tools:
  - interview__cancel
interview:
  reset:
    function: reset_onboarding
    description: Cancel onboarding and stop — do not chain next_question.
```

## Custom instructions body

```markdown
## Custom instructions

### When to use
- ...

### Session overrides
| Situation | Action |
| --------- | ------ |
| Domain-specific branch (e.g. OTP gate) | `{skill}__custom_tool()` |

Cancel, start over, message evaluation, and answer-quality rules are inherited from the base procedure — do not restate them here.

### Rules
- ...
```
