# Interview Skill — Custom Instructions (template)

The **standard interview procedure** is composed via `extends: action:jvagent/interview_action` at skill discovery. Authors write **custom instructions only** in the per-skill `SKILL.md` body — do not copy core procedure steps or enumerate fields.

Canonical base SOP: [`../SKILL.md`](../SKILL.md).

## What to put in the body

| Include | Do not include |
| ------- | -------------- |
| When to use (1–3 bullets) | Flow overview listing each field |
| Session overrides (cancel, reset) | Procedure Step 1…N per field |
| Behavioral rules (OTP gates, exists:true stop) | Duplicated core instructions |
| Custom tool callouts when non-obvious | Question wording (use `interview.questions[].description` + `next_questions`) |

## Minimal template

```markdown
---
name: my_interview
description: >-
  What this interview does and when to use it (third person).
requires-actions:
  - InterviewAction
extends: action:jvagent/interview_action
interview:
  title: My Interview
  questions: [...]
---

## Custom instructions

### When to use
- ...

### Session overrides
| Situation | Action |
| --------- | ------ |
| User cancels | `interview__cancel()` or `{skill}__reset_*` |

### Rules
- ...
```
