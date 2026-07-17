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

Declare `extends: action:jvagent/interview` and `requires-actions: [InterviewAction]`.
The framework base SOP lives in [`../SKILL.md`](../SKILL.md); composition happens at discovery.

Copy [`../examples/example_interview/`](../examples/example_interview/) as the starting template.

**Answer quality:** The inherited base procedure ([`../SKILL.md`](../SKILL.md)) is the **standard ruleset for all interview skills** — Answer quality gate, Intent routing (cancel vs start over vs answer), reply/chaining rules, and reset-tool usage. Do not duplicate these in per-skill custom instructions. Write per-field `guidance` as acceptance criteria so the model can apply the gate. See [`skill_custom_instructions.md`](skill_custom_instructions.md).

**`guidance` vs `hint` (per field).** `guidance` is **model-facing** — acceptance criteria for judging the answer. `hint` is **user-facing answer-guidance** — how to answer the question — woven into the prompt so the agent instructs the user on the intended answer (e.g. `hint: Enter your first, last, and any other names`), and surfaced in `field_reference`/`next_field` so the model can answer the user's clarifications. Phrase it as what to tell the user, non-redundant with `prompt`. See [`frontmatter-schema.md`](frontmatter-schema.md).

Full key reference: [`frontmatter-schema.md`](frontmatter-schema.md).

## Extension point overview

| Extension | Declared in | Implemented in | LLM-callable? |
|-----------|-------------|----------------|---------------|
| Builtin validator | `fields[].validator: phone` | `validators.py` | No |
| Custom validator | `fields[].validator: my_validate` | `custom_tools.py` | No |
| Pre-processor | `fields[].pre_processor: [fn]` | `custom_tools.py` | No |
| Post-processor | `fields[].post_processor: [fn]` | `custom_tools.py` | No |
| **`for_each` expansion** | `fields[].for_each` + parent `post_processor` | `custom_tools.py` (`ctx.expand_for_each`) | No — engine iterates subparts |
| Custom LLM tool | `interview.skill_tools` | `custom_tools.py` | Yes (`{skill}__{name}`) |
| Review handler | `handlers.review` | `custom_tools.py` | No |
| Reset handler | `handlers.reset` | `custom_tools.py` | No |
| Completion handler | `handlers.complete` | `custom_tools.py` | No |

**Rule:** Only `skill_tools` entries become LLM tools. Validators and processor hooks are invoked by the framework when their trigger fires. Utterance extraction is model-owned via `interview__set_fields`; the server has no extraction path — validators are the only gate.

---

## The single `ctx` interface

Every hook — validator, pre_processor, post_processor, skill_tool, handler, and
branch-condition function — takes **exactly one argument**, `ctx` (a
`HookExecutionContext`). Hooks **import nothing** from the interview package: `ctx`
is the one place a hook reads its inputs and furnishes its output. `ctx` is **always
injected and never `None`** — declare it with no default and skip the null guard.

The authoritative source is the `HookExecutionContext` docstring in
[`../hooks.py`](../hooks.py). The canonical migrated example is
[`signup_interview/scripts/custom_tools.py`](../../../../examples/jvagent_app/agents/jvagent/orchestrator_agent/skills/signup_interview/scripts/custom_tools.py)
— mirror its style.

### Inputs (attributes)

| Attribute | Purpose |
|-----------|---------|
| `ctx.value` | Raw user input — **set for validators only; `None` in post_processors** (value is already stored by then) |
| `ctx.field_def` | The `FieldDef` for the current field — available in validators, pre_processors, and **post_processors**; `None` in handlers and skill_tools |
| `ctx.session` | Read/write collected fields and `session.context` |
| `ctx.visitor` | Conversation, tasks, interaction |
| `ctx.interview` | The `InterviewAction` (`_save_session`, `_close_task`, …) |
| `ctx.config` | The interview spec |
| `ctx.extracted_values` | All collected fields (review/complete time) |
| `ctx.args` | `validator_args` / skill-tool args |
| `ctx.phase` | Lifecycle run name (activation vs storing vs advancing) |
| `ctx.activation_utterance` | The user's original activating request (or live utterance fallback) — for activation seeding without importing `ACTIVATION_UTTERANCE_KEY` |

> **Post-processor field access.** In a post_processor `ctx.value` is `None` — the
> value was stored before the hook runs. Use `ctx.session.get_value(ctx.field_def.key)`
> to read the just-stored value without hardcoding the field name. `ctx.field_def.key`
> is reliable here; prefer it over a bare string literal.

**`ctx.activation_utterance`.** On a fresh interview start (or a gated task-lock
resume before any field is stored), `handle_start` stashes the activating
`user_message`. Read it via the `ctx.activation_utterance` property — it returns
that stashed request (falling back to the live visitor utterance), so an activation
`pre_processor` can seed the first field from the user's original request without
re-parsing an aged observation, and **without importing `ACTIVATION_UTTERANCE_KEY`
or reaching into `session.context`**. The stashed value is **not** updated once
`session.fields` is non-empty.

### Output (methods)

| Method | Purpose |
|--------|---------|
| `ctx.say(msg \| [msgs], *, continue_=False, hint="")` | **The single channel for user-facing text.** One string is one question; a list is sequential statements (statement-then-followup question). `continue_=True` appends the branch-aware next-field prompt. `hint=` is model-only guidance — never shown to the user. |
| `ctx.tool_response(*, ok=None, status="ok", **data)` | The control/return envelope (`status`, `next_tool`, `interview_complete`, `value`, `retain_context_keys`, review keys, deferred `note`). **NOT** for user text. |
| `ctx.call_tool(tool)` | A control directive that chains one interview tool (no user text), e.g. `response_directive=ctx.call_tool("interview__review")`. |
| `ctx.no_session()` | The standard envelope when a hook runs without an active session. |
| `ctx.valid(value=None, **extra)` | Validator success result dict (`value` defaults to `ctx.value`). |
| `ctx.invalid(error, *, value=None, **extra)` | Validator failure result dict. `error` is stated as a plain instruction — it is auto-framed and delivered as the re-ask (same as `ctx.say`; no `Tell the user:` prefix needed). Skipped if the validator already `say`-ed or passed an explicit `response_directive`. |
| `ctx.expand_for_each(*, items=None, skip=False)` | Returns `{"for_each_expand": {...}}` for parent post-processors. `items=[{"id", "label", ...}]` starts per-item subparts; `skip=True` skips expansion. Engine-owned — not forwarded to the LLM in slim hook entries. |
| `ctx.get_for_each_records(parent_key)` | Returns the list of completed per-item records for a `for_each` parent field. Use in review and complete handlers instead of accessing `ctx.session.context["for_each"][key]["records"]` directly. Returns `[]` when the parent is unknown or expansion was skipped. |
| `ctx.start_for_each(parent_key, items=None, *, skip=False)` | Begin `for_each` expansion immediately (mutating the session). Use **only** when a *different* field's post_processor must launch a parent's subparts (e.g. a yes/no gate that expands a list collected earlier). For the parent field's own post_processor use `ctx.expand_for_each(...)` instead. |
| `ctx.infer_field_from_activation(field_key)` | Infer a field's value from `ctx.activation_utterance` via its frontmatter `validator_args.seed_from_activation` rules (longest-phrase-wins, `allowed_items` filter). Returns the matched canonical value or `None`. Wraps the module helper so skills need no interview-package import. |

### Two rules to internalize

1. **Say user text OR set a control directive — never both for the same content.**
   `ctx.say(...)` emits user text and `ctx.call_tool(...)` sets a control
   `response_directive`; using both for the same content double-emits. For "note,
   then chain a tool", use `ctx.say("note")` **plus**
   `ctx.tool_response(next_tool="interview__X")`.

2. **`ctx.say` is inert outside reply-producing phases.** On the pre_processor STORE
   re-run, branch eval, and validation, `say` records nothing — so a prompt-builder
   that re-runs while the answer is stored won't bleed its prompt onto the next turn.
   **Call `ctx.say(...)` unconditionally**; the engine binds it to the activation
   turn.

### Two different "notes" — don't confuse them

- **`ctx.say(..., hint="...")`** — `hint` is **model-only** guidance on a user
  message (e.g. "ask for the code only; do not skip"). It steers the compose model
  and is **never relayed to the user**.
- **`ctx.tool_response(ok=True, status="ok", note="...")`** — a **deferred,
  user-facing sidebar**. The framework pairs it with the authoritative next question
  computed from the *final* settled state (batch-safe — see
  [post-processors](#post-processors-after-save)). This is the only user-facing text
  a post_processor should emit, because `ctx.say` is immediate and would go stale
  when later batch fields fill in the next question.

> **Removed surface.** The old standalone directive/envelope builders and the
> directive-sink object are gone from the authoring surface: hooks import nothing
> from the interview package and never build directive strings themselves. The
> framing primitives are now internal to the engine. Use the `ctx` methods above.

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

Read the raw value from `ctx.value`; return `ctx.valid(...)` / `ctx.invalid(...)`:

```python
async def validate_rating(ctx) -> dict:
    raw = (ctx.value or "").strip()
    if raw in {"1", "2", "3", "4", "5"}:
        return ctx.valid(value=raw)
    return ctx.invalid("Please provide a rating from 1 to 5.", value=raw)
```

`ctx.valid(value=None)` defaults `value` to the raw `ctx.value`. `ctx.invalid(error,
value=…)` states the re-ask as a plain instruction — the framework auto-frames and
delivers it to the user (no `Tell the user:` prefix; same as `ctx.say`). Read
`ctx.args` for `validator_args`, and `ctx.session` to consult `session.context` or
collected fields during validation.

### Validator-side flow control

Validators may set extra keys on success via `ctx.valid(..., key=value)`:

| Key | Effect |
|-----|--------|
| `interview_complete` | Stop interview; skip `post_processor` for this field; clears session |
| `response_directive` | Tell LLM what to do next (e.g. welcome message, stop) — use `ctx.call_tool(...)` for a tool chain |
| `retain_context_keys` | List of `conversation.context` keys to keep after terminal cleanup |

Use for OTP confirmation and other cases where validating the value should finish the interview. See `validate_otp_code` in onboarding skills. Completion handlers may also return `retain_context_keys` (e.g. `user_is_onboarded`, `customer_id`).

### Builtin validators

Defined in [`../validators.py`](../validators.py): `phone`, `email`, `name`, `number`, `date`, `date_past`, `date_future`, `yes_no`, `text`, `address`, `description`, `list`.

---

## Pre-processors and post-processors

### Pre-processors (before asking)

Run when `interview__next_field()` reaches a field. Use to suggest values the system already knows.

```yaml
fields:
  - key: follow_up_email
    prompt: Follow-up email?
    pre_processor: suggest_email
```

```python
async def suggest_email(ctx) -> str:
    suggested = ctx.session.context.get("known_email")  # from context, API, etc.
    ctx.say(
        f"We have {suggested} on file. Use this for follow-up?",
        hint='If the user confirms, call interview__set_fields with '
        '{"fields": {"follow_up_email": "..."}}.',
    )
    return ctx.tool_response(ok=True, status="ok", suggested_value=suggested)
```

The LLM must **confirm** before `set_field` — a pre-tool suggestion is not a stored value.

### Surfacing extra user-visible content (a `say` list)

A field's prompt sometimes needs **more than the bare question** — an options list, a
table, a rendered summary. Pass `ctx.say` a **list** of sequential statements: the
framework delivers the leading statement(s) and then the trailing question as one
reply.

```python
async def get_available_training_times(ctx) -> str:
    slots = "\n".join(f"- {s}" for s in AVAILABLE_TRAINING_TIMES)
    # Two sequential statements: the slot list the user must see, then the question.
    ctx.say(
        [
            f"Here are the available slots:\n{slots}",
            "Which time works for you?",
        ]
    )
    return ctx.tool_response(ok=True, status="ok", available_times=AVAILABLE_TRAINING_TIMES)
```

`ctx.say` is the **single** user-text channel. It is inert outside the activation run
(see [the two rules](#two-rules-to-internalize)), so the slot list fires exactly once
— when the field is asked — and never bleeds onto a later field's reply. Call it
unconditionally; the engine binds it to the correct turn. Reference:
`get_available_training_times` in `signup_interview`.

For **model-only** steering on a user message (not content the user reads), pass
`hint=` — e.g. `ctx.say("…", hint="ask for the code only; do not skip")`.

### Post-processors (after save)

Run automatically after successful `interview__set_fields`. The LLM reads `post_tools_results`; never calls the hook manually.

```yaml
fields:
  - key: product_rating
    prompt: Rate 1-5
    post_processor: check_low_rating
```

```python
async def check_low_rating(ctx) -> str:
    rating = int(ctx.session.get_value("product_rating"))
    if rating <= 2:
        ctx.session.context["escalate"] = True
        await ctx.interview._save_session(ctx.session, ctx.visitor)
        # No user text here — chain a tool via a control directive.
        return ctx.tool_response(
            ok=True,
            status="ok",
            next_tool="interview__review",
            response_directive=ctx.call_tool("interview__review"),
        )
    return ctx.tool_response(ok=True, status="ok")
```

#### Deferred user-facing notes (the `note` key)

A post_processor must **not** bake in the next question — `set_fields` may store
several fields in one call, and a next-question computed mid-batch goes stale once a
later field fills it. To show the user a sidebar, return a **`note`** via
`ctx.tool_response`; the framework pairs it with the authoritative next question
computed from the *final* settled state. Use `note` (deferred), **not** `ctx.say`
(immediate), in a post_processor:

```python
async def append_work_email_note(ctx) -> str:
    email = (ctx.session.get_value("user_email") or "").lower()
    if "@work.com" not in email:
        return ctx.tool_response(ok=True, status="ok")
    # A NOTE (not say): the framework pairs it with the authoritative next question.
    return ctx.tool_response(
        ok=True,
        status="ok",
        note="Thank you for using your work email! We'll send you training updates.",
    )
```

#### Per-item subparts (`for_each`)

When a parent field produces a variable number of items (tracking numbers, URLs),
declare subpart templates under `for_each` and expand from the parent post-processor:

```yaml
fields:
  - key: tracking_numbers
    post_processor: check_tracking_statuses
    for_each:
      fields:
        - key: description
          prompt: What is the description?
          validator: description
        - key: invoice_value
          prompt: Invoice value in USD?
          required: false
          validator: validate_invoice_value
```

```python
async def check_tracking_statuses(ctx) -> str:
    # ctx.field_def.key is available in post_processors — use it instead of
    # hardcoding "tracking_numbers". ctx.value is None here; read from session.
    numbers = _clean_tracking_numbers(ctx.session.get_value(ctx.field_def.key) or "")
    items = [{"id": n, "label": n} for n in numbers]
    return ctx.tool_response(ok=True, **ctx.expand_for_each(items))
```

Skip expansion when no manual subparts are needed:

```python
return ctx.tool_response(ok=True, **ctx.expand_for_each(skip=True), next_tool="interview__complete")
```

**Launching subparts from a different field.** `ctx.expand_for_each(...)` is applied
by the engine only when the field being stored IS the `for_each` parent. When a
*later* field gates the expansion — e.g. a `yes/no` field that, on "yes", starts the
subparts for a list collected earlier — call `ctx.start_for_each(parent_key, items=...)`
from that field's post_processor instead. It mutates the session immediately, so a
following `ctx.say(..., continue_=True)` resolves to the first prefixed subpart prompt:

```python
async def handle_switch_to_pre_alert(ctx) -> str:
    if (ctx.session.get_value("switch_to_pre_alert") or "").lower().startswith("y"):
        new = ctx.session.context.get("new_tracking_numbers") or []
        ctx.start_for_each("tracking_numbers", items=[{"id": n, "label": n} for n in new])
        ctx.say("Great, let's create pre-alerts for those.", continue_=True)
    return ctx.tool_response(ok=True)
```

Completion and review handlers read per-item records via `ctx.get_for_each_records(parent_key)`:

```python
async def my_complete(ctx) -> str:
    records = ctx.get_for_each_records("tracking_numbers")
    # persist records…
```

Do **not** access `ctx.session.context["for_each"]["tracking_numbers"]["records"]`
directly — the internal key path is framework-private. `ctx.get_for_each_records()`
is the stable interface and returns `[]` gracefully when expansion was skipped.

See `examples/example_for_each_interview/` for a full working reference.

**v2 scope (not supported):** nested `for_each` on subpart fields, `complete_on` per item,
skill hooks for iteration boundaries, manual `session.context["collection"]` iteration.

### Hook result keys

The keys `ctx.tool_response(...)` may carry (forwarded from pre/post processor
results to the LLM via `HOOK_RESULT_KEYS` in [`../hooks.py`](../hooks.py)):

| Key | Meaning |
|-----|---------|
| `ok` / `status` | Hook outcome |
| `value` / `error` / `error_code` | Result detail |
| `system_message` | Context for the model (not a user reply) |
| `response_directive` | Control directive for this hook result (e.g. `ctx.call_tool(...)`) |
| `note` | Deferred user-facing sidebar paired with the next question |
| `next_tool` | Suggested next tool (e.g. `interview__review`) |
| `interview_complete` | Done server-side — session cleared, task closed |

**Engine-internal (not in `HOOK_RESULT_KEYS`):** `for_each_expand` — consumed by
[`engine.py`](../engine.py) after the parent post-processor runs. Use
`ctx.expand_for_each(...)`; do not expect it in `post_tools_results` slim entries.

Skill-specific signals (existing customer, OTP pending, etc.) are expressed through `response_directive` / `next_tool` / `system_message` — no special-cased keys.

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
async def example_review(ctx) -> dict:
    if ctx.session.context.get("escalate"):
        ctx.say("A team member will contact you shortly.")
        return {"terminate": True, "modified_values": {"__terminate__": "true"}}
    # Empty optional fields can be omitted from display
    return {"modified_values": {"feedback_comments": "__omit__"}}
```

| Return key | Purpose |
|------------|---------|
| `terminate` | If `true`, skip `interview__complete()` |
| `modified_values` | Display-only overrides; `__omit__` hides a field |
| `additional_data` | Extra data for completion handler |
| `custom_message` | Appended to default summary |

Emit any user-facing message with `ctx.say(...)` (e.g. the escalation notice above).

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
async def example_complete(ctx) -> str:
    # Call external APIs; set conversation.context keys before complete clears scratch
    name = (ctx.extracted_values or {}).get("user_name", "")
    ctx.say(f"Thank you, {name}! Your feedback has been recorded.")
    return ctx.tool_response(
        ok=True,
        status="ok",
        retain_context_keys=["my_persistent_flag"],  # optional
    )
```

Always deliver the user-facing message with `ctx.say(...)`. The foundation calls `clear_interview_context()` after completion — use `retain_context_keys` only for keys that must survive (platform flags, user profile markers).

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

Add custom tool names to frontmatter **`allowed-tools`** (additive — base `interview__*` tools are inherited from `extends: action:jvagent/interview`):

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

Implement the skill tool and `reset_my_interview` in `scripts/custom_tools.py`, each taking the single `ctx`. Read tool args from `ctx.args`; emit user text via `ctx.say` and control/return data via `ctx.tool_response`:

```python
async def send_otp(ctx) -> str:
    phone = ctx.session.get_value("phone_number")
    # ... send the code via an external API ...
    ctx.session.context["otp_sent"] = True
    await ctx.interview._save_session(ctx.session, ctx.visitor)
    ctx.say("I've sent a 6-digit code. What is it?", hint="ask for the code only")
    return ctx.tool_response(ok=True, status="ok")
```

The model calls **`interview__reset()`** — the foundation invokes your reset handler when `handlers.reset` is set. Return `ctx.tool_response(...)` (and `ctx.say(...)` for any user message).

Most skills use the built-in default reset (no `handlers.reset`).

Tool name on the wire: `{skill_name}__{tool.name}`.

---

## Utterance extraction (model-owned)

The model classifies each user message per the base procedure and calls `interview__set_fields` with extracted values. The server validates and stores; it does not auto-scan utterances at activation or inject `message_evaluation` tools.

Document acceptance criteria in `fields[].guidance`. Validators are the only server-side gate — tighten the validator (built-in or custom) when stricter acceptance is needed.

---

## `custom_tools.py` organization

Every hook takes the single `ctx` and imports nothing from the interview package.
The full `ctx` surface is documented in [The single `ctx`
interface](#the-single-ctx-interface) above:

```python
# Validator — raw value is ctx.value
async def validate_rating(ctx) -> dict:
    raw = (ctx.value or "").strip()
    if raw in {"1", "2", "3", "4", "5"}:
        return ctx.valid(value=raw)
    return ctx.invalid("Please give a rating from 1 to 5.", value=raw)

# Pre-processor — statement(s) then the question, in one say list
async def get_slots(ctx) -> str:
    ctx.say(["Here are the slots:\n- Mon 9am\n- Tue 2pm", "Which works for you?"])
    return ctx.tool_response(ok=True, status="ok")

# Post-processor — deferred sidebar paired with the next question
async def thank_for_work_email(ctx) -> str:
    return ctx.tool_response(ok=True, status="ok", note="Thanks for using your work email!")
```

Organize `custom_tools.py` in labeled sections (see [`signup_interview/scripts/custom_tools.py`](../../../../examples/jvagent_app/agents/jvagent/orchestrator_agent/skills/signup_interview/scripts/custom_tools.py)):

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
- [ ] Every hook takes the single `ctx`; emits user text only via `ctx.say` and control/return data via `ctx.tool_response`
- [ ] Validators return `ctx.valid` / `ctx.invalid`; use `interview_complete` when validation finishes the flow
- [ ] Post-tools use `ctx.tool_response`; user sidebars use the deferred `note` key, not `ctx.say`
- [ ] **`for_each`:** subpart keys unique vs top-level; parent post-processor returns `ctx.expand_for_each(...)`; completion reads `session.context["for_each"][parent]["records"]`
- [ ] Review uses `ctx.say` for user text; terminate path sets `terminate: true`
- [ ] Completion uses `ctx.say` and closes tasks / persists data
- [ ] Skill registered in agent `orchestrator.skills:`
- [ ] `requires-actions` lists all dependencies

See also the checklist in [../README.md](../README.md#checklist-for-a-new-skill).
