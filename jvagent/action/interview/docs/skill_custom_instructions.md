# Interview Skill — Custom Instructions (template)

The **standard interview procedure** is composed via `extends: action:jvagent/interview` at skill discovery. Authors write **custom instructions only** in the per-skill `SKILL.md` body — do not copy core procedure steps or enumerate fields.

Canonical base SOP: [`../SKILL.md`](../SKILL.md). Design contract: [platform thin-harness](../../../../docs/thin-harness.md), [interview profile](thin-harness.md) — never duplicate server steering in the skill body; put acceptance criteria in `fields[].guidance` and domain rules in custom instructions.

## What to put in the body

| Include | Do not include |
| ------- | -------------- |
| When to use (1–3 bullets) | Flow overview listing each field |
| Domain-specific session overrides | Procedure Step 1…N per field |
| Behavioral rules (OTP gates, exists:true stop) | Duplicated core instructions |
| Custom tool callouts when non-obvious | Question wording (use `interview.fields[].guidance` + `next_field`) |
| Strong `description` frontmatter for orchestrator routing | Activation / session-gate rules (`use_skill` before field questions) |
| | **Answer quality gate** (base procedure) |
| | **Model extraction** — user utterances → `interview__set_fields` per base procedure (no server prep steering) |
| | **Intent routing** — cancel vs start over vs answer vs start interview (base procedure) |
| | **Activation (session gate)** — `use_skill`, late activation (base procedure) |
| | Cancel/reset/`set_field` decision rules (base procedure) |
| | Chaining rules and `Tell the user or ask the user:` reply-only turns (base procedure) |
| | Per-field "when X appears in applicable, call set_field" restatements (base covers first missing applicable field) |

## Field guidance as acceptance criteria

Each `interview.fields[].guidance` is **model-facing acceptance criteria** — what counts as a substantive answer for that field. It is surfaced in `next_field` tool observations alongside the prompt.

Write descriptions to help the model apply the base procedure's **Answer quality gate** (see [`../SKILL.md`](../SKILL.md)):

- State what a valid answer looks like (with 1–2 examples).
- State what to reject in natural language (acknowledgements, filler, off-topic replies).
- For optional fields, note when to use `interview__skip_field` instead of `set_field`.

Example:

```yaml
- key: user_name
  prompt: "What's your full name?"
  guidance: >-
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
extends: action:jvagent/interview
# Optional: add LLM-callable custom tools (merged with base allowed-tools)
allowed-tools:
  - my_interview__send_otp
interview:
  title: My Interview
  confirm: manual
  handlers:
    reset: reset_my_interview
  fields: [...]
---
```

### `allowed-tools` and `disabled-tools` (extends merge)

When a skill declares `extends: action:jvagent/interview`, the base action's `allowed-tools` frontmatter is merged at discovery:

- **`allowed-tools` on the skill** — **additive only**. List custom LLM tools (e.g. `{skill}__send_otp`). Base `interview__*` tools are inherited automatically — do not re-list them.
- **`disabled-tools`** — Remove specific base tools from the merged set. Use when the skill must not expose a base tool (e.g. `interview__cancel` when cancel is handled via `handlers.reset` + `interview__reset`).

Example: onboarding-style cancel-and-exit via reset handler:

```yaml
extends: action:jvagent/interview
disabled-tools:
  - interview__cancel
interview:
  handlers:
    reset: reset_onboarding
    description: Cancel onboarding and stop — do not chain next_field.
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
