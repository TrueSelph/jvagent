# Interview thin harness profile

**Interview profile** of the jvagent-wide **[thin harness principle](../../../../docs/thin-harness.md)**. Platform rules apply everywhere; this doc adds interview-specific invariants for `InterviewAction`, interview skills, and maintainers extending the interview framework.

## Layer split (interview)

| Layer | Role | Thick or thin |
|-------|------|----------------|
| **Harness** (`interview_action/`, orchestrator turn-lock hooks) | Session storage, validation dispatch, processor/handler triggers, raw tool JSON, task tracking | **Thin** — no conversation steering |
| **SOP** (base [`SKILL.md`](../SKILL.md) + per-skill custom instructions) | Intent routing, turn loop, chaining rules, when to call which tool | **Thick** — model reads and follows |
| **Skill extension** (`interview:` frontmatter + `custom_tools.py`) | Field contract, domain validators, API side effects, branching | **Thick** — all business logic lives here |

The harness answers: *Is there an active session? Did validation pass? Did hooks run? What is the tool JSON?*

The model answers: *What did the user mean? What values did they provide? Which tool do I call next?*

## Interview foundation invariants (never weaken)

These extend [platform invariants](../../../../docs/thin-harness.md#platform-invariants-foundation--never-weaken) — breaking them reintroduces the pre-refactor “fat harness.”

1. **No server-side intent classification** — cancel, reset, correct/update, and multi-answer routing are defined in the composed SOP; the foundation must not use regex, keyword lists, or `control_intent` prep to choose tools for the model.

2. **No turn-prep steering** — `prepare_locked_skill_turn` returns only `runtime_ready` (session + contract loaded). It must **not** inject `interview__message_evaluation`, auto-seed `interview__next_question`, or attach `pending_directive` observations.

3. **No activation auto-store** — `_handle_start` / `on_skill_activate` must not parse the user message and pre-fill `session.fields`. Extraction is model-owned via `interview__set_fields`.

4. **No response inlining** — do not merge `next_question` or `review` payloads into `set_fields` responses inside the server (`merge_auto_next_question`, `merge_auto_review`, etc.). `next_tool` hints and `response_directive` are allowed; the model still issues separate tool calls per SOP.

5. **No orchestrator interview special-casing** — the orchestrator must not post-process interview tool results to force follow-up tool calls. Turn-lock uses generic bound-action hooks only (`skill_runtime_ready`, `prepare_locked_skill_turn`, `prune_turn_tools`).

6. **No frontmatter extractors** — utterance parsing is not declared in skill YAML. Builtin hints in [`field_extractors.py`](../core/field_extractors.py) are validation-time only, not a second extraction path.

7. **Foundation stays domain-agnostic** — no per-skill field names, signup/onboarding phrases, or business validators in `interview_action.py` / generic pipeline code. Domain fixes belong in skill `custom_tools.py`.

8. **Utterance grounding on store** — when `visitor_utterance` is present, `interview__set_fields` accepts only values extractable from that message (or a server `field_suggestion` from the active `pre_processor`). Ungrounded model values from older chat turns are rejected — do not add a second auto-extract path to compensate.

9. **Session gate via `use_skill`** — interview tools and field prompts require an active session opened by `use_skill(<skill_name>)`. Chat-only roleplay (asking `fields[].prompt` via `reply` before activation) is an SOP violation, not a harness workaround. `prune_turn_tools` hides `interview__*` when no session; `no_session_directive` and activation observations clarify the gate without per-turn steering.

## Interview skill author rules

1. **`extends: action:jvagent/interview_action`** — inherit the base turn loop; write **custom instructions only** in the skill body (see [`skill_custom_instructions.md`](skill_custom_instructions.md)).

2. **Machine contract in frontmatter** — field order, validators, processors, handlers, and `skill_tools` live under `interview:` per [`frontmatter-schema.md`](frontmatter-schema.md). Do not duplicate field lists as Procedure steps in the body.

3. **Processors and handlers are automatic** — only `interview.skill_tools` become `{skill}__{name}` LLM tools. Never list validators, `pre_processor`, `post_processor`, or `handlers.*` in `allowed-tools`.

4. **Model extracts and chains** — read the user’s latest message, call `interview__set_fields` with extracted values, read `ok` and `response_directive`, then chain `interview__next_question` / `interview__review` per base SOP — not because the server auto-called them.

5. **Acceptance criteria in `fields[].guidance`** — tell the model what counts as a substantive answer; do not add parallel `extract_*` functions for utterance parsing.

6. **Chaining gate** — never advance when `ok: false`; post-processors do not run on validation failure. When `response_directive` conflicts with `next_questions`, follow the directive.

7. **Corrections are first-class** — mid-interview and at-review updates use `interview__set_fields`; do not build server-side “correction detection.”

## Anti-patterns (reject in review)

| Anti-pattern | Why it violates thin harness |
|--------------|------------------------------|
| `extract_*_candidates` in `custom_tools.py` for standard fields | Duplicates model extraction; removed from schema (`extractors`) |
| Listing `post_processor` hooks in `skill_tools` | Hooks run on trigger; LLM must not call them manually |
| Duplicating Intent routing / turn loop / activation gate in skill body | Base SOP already composed via `extends` |
| Asking interview field prompts via `reply` without `use_skill` | No session — values cannot be stored; late activation fails grounding |
| Adding `message_evaluation` or prep observations in `InterviewAction` | Server chooses tools instead of model + SOP |
| Auto-inlining next question text into `set_fields` response | Server drives the turn; model skips explicit `next_question` call |
| Domain `if signup` branches in `interview_action.py` | Foundation absorbs skill logic |
| Regex cancel/reset detector in runtime | Intent belongs in SOP |

## Verification

Tests that guard the interview contract:

| Test area | What it proves |
|-----------|----------------|
| `test_prepare_locked_skill_turn.py` | Prep is runtime gate only — no observations |
| `test_set_fields.py` | Batch store, corrections, model-driven chaining |
| `test_set_fields_utterance_grounding.py` | Values grounded in latest user message |
| `test_frontmatter_schema_rejects_legacy_keys.py` | No `extractors` / legacy steering keys |
| `test_skill_tool_names.py` | Hooks are not LLM tools |
| `test_signup_activation_inline.py` | Activation does not auto-store; prep has no steering |

When adding interview features, extend **skill hooks or SOP** first. Touch the foundation only for generic plumbing (session keys, validator invocation, envelope shape) — and add a test that proves steering was not reintroduced.

## See also

- **[Platform thin harness](../../../../docs/thin-harness.md)** — jvagent-wide principle (read first)
- [README.md](../README.md) — architecture and reading paths
- [multi-turn-flow.md](multi-turn-flow.md) — turn-by-turn lifecycle
- [extending.md](extending.md) — validators, processors, skill tools
- [CHANGELOG.md](../../../../CHANGELOG.md) — harness strip-down release notes
