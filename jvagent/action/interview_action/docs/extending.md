# Extending Interview Skills

How to override validation, hooks, handlers, and LLM-callable tools when building a multi-turn interview skill.

> **Mandatory:** Read [platform thin-harness](../../../../docs/thin-harness.md) and the [interview profile](thin-harness.md) first. All extensions must keep the server harness thin (session + hooks + raw tools) and put intent, extraction, and domain logic in the SOP + skill package.

## Skill package layout

```
agents/<ns>/<agent>/skills/my_interview/   # required default (ADR-0023)
├── SKILL.md               # extends + interview: frontmatter; body = custom rules only
└── scripts/
    └── custom_tools.py    # Python functions referenced by function: names
```

Only use `<action_dir>/skills/my_interview/` when bundling the skill with a custom action package you distribute under `agents/.../actions/...`.

Declare `extends: action:jvagent/interview_action` and `requires-actions: [InterviewAction]`.
The framework base SOP lives in [`../SKILL.md`](../SKILL.md); composition happens at discovery.

Copy [`../examples/example_interview/`](../examples/example_interview/) as the starting template.

**Answer quality:** The inherited base procedure ([`../SKILL.md`](../SKILL.md)) is the **standard ruleset for all interview skills** — Answer quality gate, Intent routing (cancel vs start over vs answer), reply/chaining rules, and reset-tool usage. Do not duplicate these in per-skill custom instructions. Write per-field `guidance` as acceptance criteria so the model can apply the gate. See [`skill_custom_instructions.md`](skill_custom_instructions.md).

Full key reference: [`frontmatter-schema.md`](frontmatter-schema.md).

## Extension point overview

| Extension | Declared in | Implemented in | LLM-callable? |
|-----------|-------------|----------------|---------------|
| Builtin validator | `fields[].validator: phone` | `core/validators.py` | No |
| Custom validator | `fields[].validator: my_validate` | `custom_tools.py` | No |
| Pre-processor | `fields[].pre_processor: [fn]` | `custom_tools.py` | No |
| Post-processor | `fields[].post_processor: [fn]` | `custom_tools.py` | No |
| Custom LLM tool | `interview.skill_tools` | `custom_tools.py` | Yes (`{skill}__{name}`) |
| Review handler | `handlers.review` | `custom_tools.py` | No |
| Reset handler | `handlers.reset` | `custom_tools.py` | No |
| Completion handler | `handlers.complete` | `custom_tools.py` | No |

**Rule:** Only `skill_tools` entries become LLM tools. Validators and processor hooks are invoked by the framework when their trigger fires. Utterance extraction is model-owned via `interview__set_fields`; builtin patterns in [`../core/field_extractors.py`](../core/field_extractors.py) support validation-time hints only.

---

## Hook return conventions

All hooks (validators, pre/post tools, review, reset, completion) should return **`interview_tool_response(...)` JSON strings** when the response includes directives or control keys (`skip_to_review`, `interview_complete`, etc.).

| Hook type | Preferred return | Also accepted |
|-----------|------------------|---------------|
| Validator | JSON string or dict with `valid`, `value`, `error` | Tuple `(ExtractionStatus, error, value)` for legacy |
| Pre-tool | JSON string via `interview_tool_response` | Plain dict with `directive` or `response_directive` (merged by pipeline) |
| Post-tool | JSON string via `interview_tool_response` | Plain dict with control keys |
| Review / complete / reset | Dict with `directive` or `response_directive` | JSON string |

Pre-tools may return a plain dict when the payload is small (e.g. slot list + `directive`), but post-tools and validators should use `interview_tool_response()` for consistency. See `signup_interview` and `example_interview` for reference patterns.

---

## Custom validators

### Declaration (SKILL.md frontmatter `interview.fields`)

```yaml
interview:
  fields:
  - key: product_rating
    prompt: Rate the product 1-5
    validator: validate_rating   # custom_tools.py function name

  - key: follow_up_email
    prompt: Follow-up email?
    validator: email           # builtin
    validator_args:
      pattern: "^.+@.+\\..+$"
```

### Implementation (`custom_tools.py`)

Return a **JSON string** or **dict** with this shape:

```python
# Success
{"valid": True, "value": "<normalized>", "validator": "validate_rating"}

# Failure
{"valid": False, "error": "Please provide a rating from 1 to 5.", "value": "<raw>", "validator": "validate_rating"}
```

### Injected kwargs

The framework filters kwargs by function signature:

| Kwarg | Available |
|-------|-----------|
| `value` | Always (raw user input) |
| `session` | Always |
| `visitor` | When accepted |
| `interview_action` | When accepted |
| Contract `kwargs` | Merged from `validator_args` |

### Validator-side flow control

Validators may return extra keys on success:

| Key | Effect |
|-----|--------|
| `interview_complete` | Stop interview; skip `post_processor` for this field; clears session |
| `response_directive` | Tell LLM what to do next (e.g. welcome message, stop) |
| `retain_context_keys` | List of `conversation.context` keys to keep after terminal cleanup |

Use for OTP confirmation and other cases where validating the value should finish the interview. See `validate_otp_code` in onboarding skills. Completion handlers may also return `retain_context_keys` (e.g. `user_is_onboarded`, `customer_id`).

### Builtin validators

Defined in [`../core/validators.py`](../core/validators.py): `phone`, `email`, `name`, `number`, `date`, `date_past`, `date_future`, `yes_no`, `text`, `address`, `description`, `list`.

---

## Pre-processors and post-processors

### Pre-processors (before asking)

Run when `interview__next_question()` reaches a field. Use to suggest values the system already knows.

```yaml
fields:
  - key: follow_up_email
    prompt: Follow-up email?
    pre_processor: suggest_email
```

```python
async def suggest_email(session=None, visitor=None, **kwargs) -> dict:
    suggested = "user@example.com"  # from conversation.context, API, etc.
    return {
        "ok": True,
        "suggested_value": suggested,
        "directive": tell_user_directive(
            f"We have {suggested} on file. Use this for follow-up?",
            note="If user confirms, call interview__set_field(field='follow_up_email', value='...')",
        ),
    }
```

The LLM must **confirm** before `set_field` — a pre-tool suggestion is not a stored value.

### Post-processors (after save)

Run automatically after successful `interview__set_fields`. The LLM reads `post_tools_results`; never calls the hook manually.

```yaml
fields:
  - key: product_rating
    prompt: Rate 1-5
    post_processor: check_low_rating
```

```python
async def check_low_rating(session=None, interview_action=None, **kwargs) -> str:
    rating = int(session.get_value("product_rating"))
    if rating <= 2:
        session.context["escalate"] = True
        await interview_action._save_session(session, visitor)
        return interview_tool_response(
            ok=True,
            skip_to_review=True,
            response_directive=call_tool_directive("interview__review"),
        )
    return interview_tool_response(ok=True, skip_to_review=False)
```

### Post-tool result keys

Exposed via `POST_TOOL_RESULT_KEYS` in [`../core/responses.py`](../core/responses.py):

| Key | Meaning |
|-----|---------|
| `skip_to_review` | Jump to `interview__review()` |
| `interview_complete` | Done server-side — stop |
| `exists` | Entity already exists — stop |
| `otp_pending` | OTP required before asking for code |
| `next_tool` | Suggested next tool |
| `response_directive` | Override for this hook result |

Prefer `interview_tool_response()` from `core/responses.py` for consistent envelopes.

---

## Review handler

Optional. Default: built-in field summary.

```yaml
interview:
  review:
    function: example_review
    description: "Escalation or confirmation summary"
```

```python
async def example_review(
    session=None,
    extracted_values=None,
    review_data=None,
    **kwargs,
) -> dict:
    if session.context.get("escalate"):
        return {
            "directive": "A team member will contact you shortly.",
            "terminate": True,
            "modified_values": {"__terminate__": "true"},
        }
    # Empty optional fields can be omitted from display
    return {"modified_values": {"feedback_comments": "__omit__"}}
```

| Return key | Purpose |
|------------|---------|
| `directive` | User-facing message (required for custom behavior) |
| `terminate` | If `true`, skip `interview__complete()` |
| `modified_values` | Display-only overrides; `__omit__` hides a field |
| `additional_data` | Extra data for completion handler |
| `custom_message` | Appended to default summary |

---

## Completion handler

Required on every interview skill (`interview.completion` in frontmatter).

```yaml
interview:
  completion:
  function: example_complete
  description: "Persist data after user confirms review"
```

```python
async def example_complete(
    session=None,
    visitor=None,
    interview_action=None,
    extracted_values=None,
    **kwargs,
) -> dict:
    # Call external APIs; set conversation.context keys before complete clears scratch
    return {
        "directive": "Thank you! Your feedback has been recorded.",
        "retain_context_keys": ["my_persistent_flag"],  # optional
    }
```

Always return a `directive` string the LLM delivers to the user. The foundation calls `clear_interview_context()` after completion — use `retain_context_keys` only for keys that must survive (platform flags, user profile markers).

---

## LLM-callable custom tools

For operations the LLM must initiate (send OTP, custom reset, process image):

```yaml
interview:
  tools:
  - name: send_otp
    description: "Send verification code when user confirms phone..."
    function: send_otp
    parameters: {}
```

Add custom tool names to frontmatter **`allowed-tools`** (additive — base `interview__*` tools are inherited from `extends: action:jvagent/interview_action`):

```yaml
allowed-tools:
  - my_interview__send_otp
```

To **override** the base reset (restart, cancel-and-exit, or other skill-specific behavior), declare a reset handler like review/completion:

```yaml
interview:
  handlers:
    reset: reset_my_interview
```

Implement `reset_my_interview` in `scripts/custom_tools.py`. The model calls **`interview__reset()`** — the foundation invokes your handler when `handlers.reset` is set. Return `interview_tool_response(...)` or a dict with `response_directive` / `status`.

Most skills use the built-in default reset (no `handlers.reset`).

Tool name on the wire: `{skill_name}__{tool.name}`.

---

## Utterance extraction (model-owned)

The model classifies each user message per the base procedure and calls `interview__set_fields` with extracted values. The server validates and stores; it does not auto-scan utterances at activation or inject `message_evaluation` tools.

Document acceptance criteria in `fields[].guidance`. Builtin validation-time hints live in [`../core/field_extractors.py`](../core/field_extractors.py) (email, phone, date patterns keyed by validator name).

---

## Response helpers

Use [`../core/responses.py`](../core/responses.py) — do not invent ad-hoc directive formats:

```python
from jvagent.action.interview_action.core.responses import (
    call_tool_directive,
    interview_tool_response,
    tell_user_directive,
    no_session_directive,
)

tell_user_directive("What is your name?")
tell_user_with_followup_directive("Thanks.", present_field="phone_number")  # chains next ask
call_tool_directive("interview__review")
interview_tool_response(ok=True, status="ok", skip_to_review=True, ...)
```

Post-tools may set `present_field` so turn-prep does not re-seed the same question.

---

## Function signature reference

`_load_custom_function()` injects kwargs only when the callable accepts them:

| Kwarg | Typical use |
|-------|-------------|
| `session` | Read/write collected fields and `session.context` |
| `visitor` | Access conversation, tasks, interaction |
| `interview_action` | `_save_session`, `_close_task`, `_handle_start` |
| `config` | Review/completion handlers |
| `extracted_values` | All collected fields at review/complete time |
| `review_data` | Review-stage snapshot |

Organize `custom_tools.py` in labeled sections (see [`../examples/example_interview/scripts/custom_tools.py`](../examples/example_interview/scripts/custom_tools.py)):

1. Constants
2. Shared helpers
3. Validators
4. Pre-tools
5. Post-tools + LLM tools
6. Review handler
7. Completion handler

---

## Checklist for a new skill

- [ ] `SKILL.md` `name` matches folder; frontmatter includes `interview:` block
- [ ] Every `function:` has an implementation in `custom_tools.py`
- [ ] Processors and handlers are **not** in `interview.skill_tools`
- [ ] LLM tools are in both `interview.skill_tools` and frontmatter `allowed-tools` (additive; do not re-list base `interview__*` tools)
- [ ] `SKILL.md` body is custom rules only (no per-field Procedure steps)
- [ ] Validators return correct shape; use `interview_complete` when validation finishes the flow
- [ ] Post-tools use `interview_tool_response` with clear `response_directive`
- [ ] Review returns `directive`; terminate path sets `terminate: true`
- [ ] Completion returns `directive` and closes tasks / persists data
- [ ] Skill registered in agent `orchestrator.skills:`
- [ ] `requires-actions` lists all dependencies

See also the checklist in [../README.md](../README.md#checklist-for-a-new-skill).
