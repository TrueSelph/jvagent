# Interview frontmatter schema (`interview:`)

Canonical shape for the `interview:` block in skill `SKILL.md` frontmatter. Only keys listed below are accepted — unknown keys fail at parse time.

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
| `guidance` | No | Acceptance criteria for judging the **answer** (model-facing extraction/validation grounding) |
| `hint` | No | Plain **answer-guidance for the user** — how to answer this question (e.g. `Enter your first, last, and any other names`; an accepted format; that a field is optional). Woven into the prompt so the agent instructs the user on the intended answer, and surfaced in `field_reference` / `next_field` so the model can answer the user's clarifications about the field. Phrase it as what to tell the user, one line, **non-redundant with `prompt`** (overlap gets deduped). Distinct from `guidance` (model-facing, judges the answer). |
| `validator` | No | Validator function name (string) |
| `validator_args` | No | Kwargs passed to the validator |
| `pre_processor` | No | Function name or list — runs before asking |
| `post_processor` | No | Function name or list — runs after successful store |
| `branches` | No | Conditional routing — see below |
| `else` | No | Default next field when no branch matches |
| `for_each` | No | Per-item subpart field templates — see below |
| `for_each_prefix` | No | Function name in `scripts/custom_tools.py` that returns a custom prompt prefix for each for_each iteration. Receives `(index, total, label, field_key, field_value)`. Overrides the default prefix. When omitted, the default is `"For {singular_key} {value}:"` for single items and `"For the {ordinal} {singular_key} {value}:"` for multiple items. |

### Per-item subparts (`fields[].for_each`)

Declare subpart questions the engine asks once per item after the parent field stores.
The parent `post_processor` (or activation `pre_processor` on gated resume) returns
`for_each_expand` via `ctx.expand_for_each(items=[...])` or `ctx.expand_for_each(skip=True)`.

| Key | Required | Description |
|-----|----------|-------------|
| `fields` | Yes | Ordered subpart field definitions (same keys as top-level fields except no `branches` / `else` / nested `for_each`) |

The default prompt prefix for each iteration is derived from the parent field key:
- **Single item**: `"For {singularized_key} {value}:"` (e.g. `"For tracking number 92492387:"`)
- **Multiple items**: `"For the {ordinal} {singularized_key} {value}:"` (e.g. `"For the first tracking number 92492387:"`)

To customize the prefix, add `for_each_prefix: function_name` on the parent field. The
function receives `(index, total, label, field_key, field_value)` and returns the full
prefix string.

Child field keys must not collide with any top-level field key. Collected per-item
data lives in `session.context["for_each"][parent_key]["records"]`.

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
    - key: phone_number
      prompt: "What is your phone number?"
      required: false
      hint: Enter a mobile number with country and area code; optional, so you may skip.
      validator: phone
  handlers:
    review: signup_review
    complete: signup_complete
```

### `for_each` subparts (excerpt)

```yaml
interview:
  fields:
    - key: tracking_numbers
      prompt: Enter tracking numbers (comma-separated).
      post_processor: check_tracking_statuses
      for_each:
        fields:
          - key: description
            prompt: What is the description?
            required: true
          - key: invoice_value
            prompt: What is the invoice value?
            required: true
```

Child keys (`description`, `invoice_value`) must not collide with other top-level field keys.
Collected data: `session.context["for_each"]["tracking_numbers"]["records"]`.

### Gated-resume seeding (`seed_from_activation`)

**Invariant I-INT-SEED-01:** trigger phrases for seeding a field from
`session.context["activation_utterance"]` belong in frontmatter, not skill Python.

On fields with `validator_args.seed_from_activation`, declare canonical values → trigger
phrases and use built-in pre_processor `seed_field_from_activation`:

```yaml
    - key: interview_intent
      validator: list
      validator_args:
        allowed_items: [check_status, create_pre_alert]
        seed_from_activation:
          create_pre_alert: [pre-alert, pre alert, create a pre]
          check_status: [check status, where is my package]
      pre_processor:
        - seed_field_from_activation
```

Matching: longest phrase wins; ties → earlier YAML key. Matched values must appear in
`allowed_items` when declared. Downstream hooks may call
`infer_field_from_activation(session, field_def, visitor)` for the same rules.

## Strict parsing

The parser accepts only the keys documented in this file. Typos and obsolete key names (e.g. `questions`, `pre_tools`, `tools`) raise `Unknown frontmatter key '…'`. Validators must be function-name strings plus optional `validator_args` — not nested mappings.
