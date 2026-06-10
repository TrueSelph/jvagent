# Troubleshooting

Common issues when building or running skills-v2 interviews with `InterviewAction`.

If a fix would add server-side turn steering (prep observations, auto-store, extractors, inlined next-question merges), **stop** — that violates the [thin harness principle](../../../../docs/thin-harness.md) and [interview profile](thin-harness.md). Extend the SOP or skill hooks instead.

## Skill not discovered / tools missing

**Symptom:** `use_skill` fails or interview tools not available.

**Causes:**
- Skill lives under `example/` instead of `skills/` — `InterviewRegistry.discover()` only scans `skills/`.
- `InterviewAction` not enabled in agent actions.
- Skill not listed in orchestrator `skills:` in `agent.yaml`.
- `requires-actions: [InterviewAction]` missing from `SKILL.md` frontmatter.

**Fix:** Copy skill to `skills/<name>/`, enable action, register skill, align frontmatter.

---

## Hook function exposed as LLM tool

**Symptom:** Model tries to call `verify_phone_number` directly.

**Cause:** Function listed in frontmatter `interview.skill_tools` when it should only be a `post_processor` hook.

**Fix:** Remove from `skill_tools`. Only declare LLM-initiated operations (send OTP, process image) as skill tools. Processors, validators, and `handlers.*` run automatically on the appropriate path.

---

## Post-processors never run

**Symptom:** Branching logic in post-tool ignored.

**Causes:**
- `interview__set_fields` returned `ok: false` (validation failed).
- Validator returned `interview_complete: true` (post-processors skipped by design).
- LLM called hook function manually instead of `set_field`.

**Fix:** Ensure validation passes; read `ok` before advancing. Document in `SKILL.md` that hooks are automatic.

---

## Chat-only interview roleplay (no `use_skill`)

**Symptom:** Model asks interview field prompts via `reply` for several turns without calling `use_skill` or `interview__*` tools; later activates the skill and re-asks fields the user already provided in chat.

**Causes:**
- Model paraphrased `fields[].prompt` or skill `description` without opening a session.
- `use_skill` delayed until a later turn; per base SOP, values from pre-activation chat turns are not reused.
- Per-skill body duplicated activation rules weakly or inconsistently instead of relying on composed base SOP.

**Fix:** Base **Activation (session gate)** in [`SKILL.md`](../SKILL.md): `use_skill` → `interview__next_field` (or activation `set_fields`) before field questions. Strengthen skill `description` for orchestrator routing; keep domain rules only in custom instructions. On `NO_SESSION`, follow `response_directive` — do not compensate with chat-only questions.

---

## LLM asks wrong question / skips fields

**Symptom:** Questions out of order or optional fields skipped silently.

**Causes:**
- LLM ignored `next_fields` or followed stale `next_fields` from a prior turn.
- `response_directive` conflict not resolved (directive wins).
- Procedure in `SKILL.md` unclear about optional field handling.

**Fix:** Strengthen `SKILL.md` reply rules. Enforce "one action per turn" and "never reuse old field values." Call `interview__get_status()` to recover state.

---

## Pre-tool suggestion saved without confirmation

**Symptom:** Email/phone stored without user confirming.

**Cause:** LLM replied with the suggested value instead of calling `interview__set_fields`.

**Fix:** In `SKILL.md`, state explicitly: "When pre_processor suggests a value, ask user to confirm, then call `interview__set_fields` on their next message."

---

## Validation always fails

**Symptom:** `ok: false` on every `set_field`.

**Causes:**
- Custom validator returns wrong shape (missing `valid` key).
- Validator function name mismatch between frontmatter `interview.fields[].validator` and `custom_tools.py`.
- Builtin validator kwargs wrong (e.g. `exact_length: 10` on phone).

**Fix:** Match function names exactly. Return `{"valid": True/False, "value": ..., "error": ...}`. Test with `pytest tests/action/interview_action/test_interview_set_field_validation.py`.

---

## Review called too early

**Symptom:** Summary shown before optional fields handled.

**Cause:** LLM called `interview__review()` while `next_fields` still had items.

**Fix:** Procedure should require empty `next_fields` (or explicit `skip_field` for optional items) before review.

---

## Complete called without review

**Symptom:** Completion handler runs without user seeing summary.

**Fix:** Add to `SKILL.md` critical rules: "Always call `interview__review()` before `interview__complete()` unless review sets `terminate: true`."

---

## Session persists after complete/cancel

**Symptom:** Old field values or scratch keys (`signup_records`, `otp_pending`) appear after the interview ends.

**Causes:**
- LLM reused values from chat history instead of fresh session.
- Skill wrote interview scratch to `conversation.context` without `retain_context_keys`.
- Custom reset tool did not call `_clear_interview_session`.

**Fix:** After complete/cancel, require `use_skill` to start fresh. Persist only platform/profile keys via `retain_context_keys` on completion handlers or `interview_complete` validators. For custom reset behavior, set `handlers.reset` in frontmatter (routed by `handle_reset` in `engine.py`). Most skills use the built-in default via `interview__reset()` with no `handlers.reset`.

---

## Duplicate `interview__next_field` calls

**Symptom:** Turn calls `next_field` multiple times; user sees duplicate asks.

**Cause:** LLM chains `interview__next_field` again after already receiving the question directive this turn.

**Fix:** The standard procedure (composed via extends) covers chaining — one `next_field` per turn; do not call it again until after `set_fields` returns `ok:true`.

---

## Turn-lock drops interview tools

**Symptom:** On locked turns, `interview__*` tools not visible.

**Cause:** `prune_turn_tools` hides tools when `skill_runtime_ready` fails (session not loaded).

**Fix:** Ensure prior turn completed skill activation; check for session in `conversation.context["interview"]`.

---

## Model extracts wrong value

**Symptom:** `interview__set_fields` stores an incorrect substring from the user message.

**Fix:** Tighten `fields[].guidance` and custom validators — the model owns utterance extraction via `interview__set_fields`; validators are the only server-side gate and there is no frontmatter `extractors` block.

---

## External API errors in completion

**Symptom:** User sees generic error after confirming review.

**Fix:** Return clear `directive` from completion handler on failure; use `session.context` to store error details for retry. See `complete_onboarding` patterns in live skills.

---

## Tests

```bash
pytest tests/action/interview_action/ -v
```

Key test files:
- `test_interview_set_field_validation.py` — validators
- `test_skill_tool_names.py` — hook vs tool exposure
- `test_interview_next_field.py` — pre-tools
- `test_interview_skill_activate.py` — contract loading
