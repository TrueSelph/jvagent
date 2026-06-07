# Interview Skill — Custom Instructions (template)

The **standard interview procedure** is injected automatically by `interview_action` when the orchestrator discovers a skill with `requires-actions: [InterviewAction]` and a frontmatter `interview:` block. Authors write **Custom instructions only** in `SKILL.md` body — do not copy core procedure steps or enumerate fields.

Canonical standard procedure: [`standard_procedure.md`](standard_procedure.md) (loaded via `jvagent.action.interview_action.procedure`).

## What to put in the body

| Include | Do not include |
| ------- | -------------- |
| When to use (1–3 bullets) | Flow overview listing each field |
| Session overrides (cancel, reset) | Procedure Step 1…N per field |
| Behavioral rules (OTP gates, exists:true stop) | Duplicated core instructions |
| Custom tool callouts when non-obvious | Question wording (use `interview.questions[].description` + `next_questions`) |

## Example skeleton

```markdown
## Custom instructions

### When to use

- User wants to …

### Rules

1. …

### Session overrides

| Situation | Action |
| --------- | ------ |
| User cancels | `my_skill__reset()` or `interview__cancel()` |

### Tone

Friendly and concise. Bold only the question text from `next_questions`.
```

Per-field detail belongs in frontmatter `interview.questions[]` and runtime tool responses (`next_questions`, `post_tools_results`, `response_directive`).
