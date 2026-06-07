# Troubleshooting

Common issues when building or running skills-v2 interviews with `InterviewAction`.

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

**Cause:** Function listed in frontmatter `interview.tools` when it should only be a `post_tools` hook.

**Fix:** Remove from `interview.tools`. Only declare LLM-initiated operations (send OTP, reset) as tools. Hooks run automatically on `set_field` / `next_question`.

---

## `post_tools` never run

**Symptom:** Branching logic in post-tool ignored.

**Causes:**
- `interview__set_field` returned `ok: false` (validation failed).
- Validator returned `interview_complete: true` (post_tools skipped by design).
- LLM called hook function manually instead of `set_field`.

**Fix:** Ensure validation passes; read `ok` before advancing. Document in `SKILL.md` that hooks are automatic.

---

## LLM asks wrong question / skips fields

**Symptom:** Questions out of order or optional fields skipped silently.

**Causes:**
- LLM ignored `next_questions` or followed stale `next_questions` from a prior turn.
- `response_directive` conflict not resolved (directive wins).
- Procedure in `SKILL.md` unclear about optional field handling.

**Fix:** Strengthen `SKILL.md` reply rules. Enforce "one action per turn" and "never reuse old field values." Call `interview__get_status()` to recover state.

---

## Pre-tool suggestion saved without confirmation

**Symptom:** Email/phone stored without user confirming.

**Cause:** LLM replied with the suggested value instead of calling `interview__set_field`.

**Fix:** In `SKILL.md`, state explicitly: "When pre_tools suggests a value, ask user to confirm, then call `set_field` on their next message."

---

## Validation always fails

**Symptom:** `ok: false` on every `set_field`.

**Causes:**
- Custom validator returns wrong shape (missing `valid` key).
- Validator function name mismatch between frontmatter `interview.questions` and `custom_tools.py`.
- Builtin validator kwargs wrong (e.g. `exact_length: 10` on phone).

**Fix:** Match function names exactly. Return `{"valid": True/False, "value": ..., "error": ...}`. Test with `pytest tests/action/interview_action/test_interview_set_field_validation.py`.

---

## Review called too early

**Symptom:** Summary shown before optional fields handled.

**Cause:** LLM called `interview__review()` while `next_questions` still had items.

**Fix:** Procedure should require empty `next_questions` (or explicit `skip_field` for optional items) before review.

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

**Fix:** After complete/cancel, require `use_skill` to start fresh. Persist only platform/profile keys via `retain_context_keys` on completion handlers or `interview_complete` validators. For reset tools, call `_clear_interview_session` then re-init (see `reset_signup_interview`).

---

## Triple `interview__next_question` on activation

**Symptom:** First turn calls `next_question` multiple times; user sees duplicate asks.

**Cause:** LLM calls `interview__next_question` after turn prep already seeded the first question.

**Fix:** The standard procedure (composed via extends) covers turn-prep — reply from activation `response_directive`; do not call `next_question` again until after `set_field` returns `ok:true`.

---

## Turn-lock drops interview tools

**Symptom:** On locked turns, `interview__*` tools not visible.

**Cause:** `prune_turn_tools` hides tools when `skill_runtime_ready` fails (session not loaded).

**Fix:** Ensure prior turn completed skill activation; check for session in `conversation.context["interview"]`.

---

## Field seeding wrong value

**Symptom:** Opening message seeds incorrect field.

**Fix:** Adjust regex/logic in `core/field_extractors.py` for your validator name, or disable seeding by not adding an extractor branch.

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
- `test_interview_next_question.py` — pre-tools
- `test_interview_skill_activate.py` — contract loading
