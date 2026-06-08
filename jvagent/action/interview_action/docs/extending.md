# Extending Interview Skills

How to override validation, hooks, handlers, and LLM-callable tools when building a multi-turn interview skill.

## Skill package layout

```
<action_dir>/skills/my_interview/          # core or app overlay
├── SKILL.md               # extends + interview: frontmatter; body = custom rules only
└── scripts/
    └── custom_tools.py    # Python functions referenced by function: names
```

App overlay path: `agents/<ns>/<agent>/actions/jvagent/interview_action/skills/my_interview/`.

Declare `extends: action:jvagent/interview_action` and `requires-actions: [InterviewAction]`.
The framework base SOP lives in [`../SKILL.md`](../SKILL.md); composition happens at discovery.

Copy [`../examples/example_interview/`](../examples/example_interview/) as the starting template.

**Answer quality:** The inherited base procedure ([`../SKILL.md`](../SKILL.md)) is the **standard ruleset for all interview skills** — Answer quality gate, Intent routing (cancel vs start over vs answer), reply/chaining rules, and reset-tool usage. Do not duplicate these in per-skill custom instructions. Write per-question `description` fields as acceptance criteria so the model can apply the gate. See [`skill_custom_instructions.md`](skill_custom_instructions.md).

## Extension point overview

| Extension | Declared in | Implemented in | LLM-callable? |
|-----------|-------------|----------------|---------------|
| Builtin validator | `question.validator.function: phone` | `core/validators.py` | No |
| Custom validator | `question.validator.function: my_validate` | `custom_tools.py` | No |
| Pre-tool | `question.pre_tools: [fn]` | `custom_tools.py` | No |
| Post-tool | `question.post_tools: [fn]` | `custom_tools.py` | No |
| Custom LLM tool | `interview.tools` | `custom_tools.py` | Yes (`{skill}__{name}`) |
| Review handler | `interview.review.function` | `custom_tools.py` | No |
| Reset handler | `interview.reset.function` | `custom_tools.py` | No |
| Completion handler | `interview.completion.function` | `custom_tools.py` | No |
| Field seeding | (implicit via validator name) | `core/field_extractors.py` | No |
| `@interview_tool` decorator | — | `custom_tools.py` | Yes (legacy discovery) |

**Rule:** Only `interview.tools` entries become LLM tools. Validators and hooks are invoked by the framework when their trigger fires.

---

## Custom validators

### Declaration (SKILL.md frontmatter `interview.questions`)

```yaml
interview:
  questions:
  - name: product_rating
    validator:
      function: validate_rating   # custom_tools.py function name

  - name: follow_up_email
    validator:
      function: email           # builtin
      kwargs:
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
| Contract `kwargs` | Merged from `validator.kwargs` |

### Validator-side flow control

Validators may return extra keys on success:

| Key | Effect |
|-----|--------|
| `interview_complete` | Stop interview; skip `post_tools` for this field; clears session |
| `response_directive` | Tell LLM what to do next (e.g. welcome message, stop) |
| `retain_context_keys` | List of `conversation.context` keys to keep after terminal cleanup |

Use for OTP confirmation and other cases where validating the value should finish the interview. See `validate_otp_code` in onboarding skills. Completion handlers may also return `retain_context_keys` (e.g. `user_is_onboarded`, `customer_id`).

### Builtin validators

Defined in [`../core/validators.py`](../core/validators.py): `phone`, `email`, `name`, `number`, `date`, `date_past`, `date_future`, `yes_no`, `text`, `address`, `description`, `list`.

---

## Pre-tools and post-tools

### Pre-tools (before asking)

Run when `interview__next_question()` reaches a field. Use to suggest values the system already knows.

```yaml
questions:
  - name: follow_up_email
    pre_tools:
      - suggest_email
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

### Post-tools (after save)

Run automatically after successful `interview__set_field`. The LLM reads `post_tools_results`; never calls the hook manually.

```yaml
questions:
  - name: product_rating
    post_tools:
      - check_low_rating
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
  reset:
    function: reset_my_interview
    description: >-
      When user wants to start over. Clears session and re-asks the first question
      with a custom message.
```

Implement `reset_my_interview` in `scripts/custom_tools.py`. The model calls **`interview__reset_interview()`** — the foundation invokes your handler when `reset.function` is set. Return `interview_tool_response(...)` or a dict with `response_directive` / `status`.

Most skills use the built-in default reset (no `interview.reset` block).

Tool name on the wire: `{skill_name}__{tool.name}`.

### Legacy `@interview_tool` decorator

Prefer frontmatter `interview.tools`. The decorator in [`../decorators.py`](../decorators.py) remains for auto-discovery when a function is not listed in the spec.

---

## Field seeding (opening message)

Add branches in [`../core/field_extractors.py`](../core/field_extractors.py) `extract_candidates_for_question()` keyed by validator function name. Called once on skill activation.

Built-in extraction: `email`, `phone`, `date_past`. Custom examples: `validate_tracking_number`, `validate_id_number`.

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
- [ ] Hooks are **not** in `interview.tools`
- [ ] LLM tools are in both `interview.tools` and frontmatter `allowed-tools` (additive; do not re-list base `interview__*` tools)
- [ ] `SKILL.md` body is custom rules only (no per-field Procedure steps)
- [ ] Validators return correct shape; use `interview_complete` when validation finishes the flow
- [ ] Post-tools use `interview_tool_response` with clear `response_directive`
- [ ] Review returns `directive`; terminate path sets `terminate: true`
- [ ] Completion returns `directive` and closes tasks / persists data
- [ ] Skill registered in agent `orchestrator.skills:`
- [ ] `requires-actions` lists all dependencies

See also the checklist in [../README.md](../README.md#checklist-for-a-new-skill).
