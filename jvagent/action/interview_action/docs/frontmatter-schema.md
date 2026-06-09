# Interview frontmatter schema (`interview:`)

Canonical shape for the `interview:` block in skill `SKILL.md` frontmatter. Legacy keys are rejected at parse time.

Schema choices follow the **[thin harness principle](../../../../docs/thin-harness.md)** and [interview profile](thin-harness.md): no `extractors` (model extracts via `interview__set_fields`); processors and handlers are automatic triggers, not LLM tools (`skill_tools` only).

## Top-level keys

| Key | Required | Description |
|-----|----------|-------------|
| `title` | No | Display title for the interview |
| `summary` | No | Contract-level description for authors and tooling |
| `confirm` | No | `manual` (default) or `auto` — see [Confirm mode](#confirm-mode) |
| `fields` | Yes | Ordered list of field definitions |
| `handlers` | No | Lifecycle handler function names |
| `skill_tools` | No | LLM-callable tools (`{skill}__{name}`) |

### Handlers (`handlers:`)

| Key | Value |
|-----|-------|
| `review` | Function name in `scripts/custom_tools.py` |
| `complete` | Function name in `scripts/custom_tools.py` |
| `reset` | Function name in `scripts/custom_tools.py` |
| `cancel` | Function name in `scripts/custom_tools.py` |

Each value is a **string** (function name), not a nested `{ function: … }` object.

### Skill tools (`skill_tools[]`)

| Key | Purpose |
|-----|---------|
| `name` | Tool surface name → `{skill}__{name}` |
| `function` | `custom_tools.py` function name |
| `description` | Tool description for the model |
| `parameters` | JSON schema (optional) |

## Per-field keys (`fields[]`)

| Key | Required | Description |
|-----|----------|-------------|
| `key` | Yes | Field identifier (used with `set_fields` / `get_fields`) |
| `prompt` | Yes | Question text shown to the user |
| `required` | No | Default `true` |
| `guidance` | No | Acceptance criteria / author notes for the model |
| `validator` | No | Validator function name (string) |
| `validator_args` | No | Kwargs passed to the validator |
| `pre_processor` | No | Function name or list — runs before asking |
| `post_processor` | No | Function name or list — runs after successful store |
| `input_handler` | No | Normalizes raw input before validation |
| `branches` | No | Conditional routing — see below |
| `else` | No | Default next field when no branch matches |

### Branches (`fields[].branches[]`)

| Key | Description |
|-----|-------------|
| `when` | Condition object (same ops as before) |
| `goto` | Target field `key` |

## Confirm mode

```yaml
interview:
  confirm: manual   # default — wait for user yes at review
  # confirm: auto   # chain interview__complete after review in same turn
```

- **`manual`**: Review shows a summary and waits for explicit user confirmation before `interview__complete`.
- **`auto`**: After `interview__review`, the response sets `next_tool: interview__complete` and a directive that tells the model not to ask for confirmation. The model still makes two tool calls (`review` → `complete`); the server does not auto-run `complete` inside `review`.

`interview__get_status` and review responses include `confirm` so the base SOP can branch.

## Example

```yaml
interview:
  title: JVAgent Training Signup
  summary: Collect name, slot, email, optional phone.
  confirm: manual
  fields:
    - key: user_name
      prompt: "What's your full name?"
      required: true
      guidance: First and last name only — not filler.
      validator: validate_full_name
    - key: user_email
      prompt: "What is your email?"
      required: true
      post_processor: append_work_email_note
      validator: validate_signup_email
  handlers:
    review: signup_review
    complete: signup_complete
```

## Removed (breaking)

| Old key | Replacement |
|---------|---------------|
| `description` (top-level) | `summary` |
| `questions` | `fields` |
| `name` / `question` / `description` (per-field) | `key` / `prompt` / `guidance` |
| `pre_tools` / `post_tools` | `pre_processor` / `post_processor` |
| `validator: { function, kwargs }` | `validator` + `validator_args` |
| `review` / `completion` / `reset` / `cancel` (top-level) | `handlers.*` |
| `tools` | `skill_tools` |
| `extractors` | *(removed — model extracts via `set_fields`)* |
| `default_next` | `else` |
| `branches[].condition` / `target` | `when` / `goto` |
