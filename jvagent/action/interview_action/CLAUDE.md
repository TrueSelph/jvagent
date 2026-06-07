# interview_action — Agent Guide

---

## What this is

`InterviewAction` is a pure `Action` (not `InteractAction`) that registers eight fixed `interview__*` tools plus per-skill custom tools. The **orchestrator LLM** reads each skill's `SKILL.md` procedure and drives multi-turn interviews by calling tools — the action manages session state, validation, hooks, and task tracking; it does **not** classify user intent or choose the next question itself.

Session state lives in `conversation.context["interview"]` as a lightweight `InterviewSession` dataclass (field values, skipped fields, status, scratch `context` dict).

---

## Foundation vs skill extensions

**`interview_action/` is a reusable foundation.** It must stay domain-agnostic: no signup/training phrases, no per-skill field names, no business validators hardcoded in `interview_action.py`.

| Layer | Location (per consuming app) | Owns |
|-------|------------------------------|------|
| **Foundation** | `jvagent/action/interview_action/` | `interview__*` tools, session lifecycle, hook dispatch, validator *invocation*, turn-prep seeding, generic pipeline |
| **Spec** | `skills/<name>/SKILL.md` frontmatter `interview:` | Questions, order, branches, validator `function:` refs, pre/post/review/complete hooks |
| **Procedure** | `procedure.py` + `skills/<name>/SKILL.md` body | Standard tool loop injected at discovery; body = custom behavioral rules only |
| **Implementation** | `skills/<name>/scripts/custom_tools.py` | Validators, pre/post tools, completion handlers, custom LLM tools |

When fixing behavior for one skill (e.g. `validate_full_name`, training slot matching), change the **skill extension** — not the foundation — unless the bug is in generic plumbing (chaining, utterance-vs-model validation, hook dispatch, session keys like `CTX_QUESTION_PRESENTED`).

**Terminal cleanup:** `complete`, `cancel`, and `interview_complete` validators call `clear_interview_context()` — wipes `conversation.context` except platform keys (`new_user`) and any `retain_context_keys` returned by the completion handler or validator. Do not persist interview scratch in `conversation.context` unless opting in via `retain_context_keys`.

---

## File map

```
interview_action/
├── interview_action.py   # InterviewAction — session, hooks, skill activation
├── interview_loader.py    # InterviewRegistry, SKILL.md frontmatter parsing
├── tools.py              # interview__* tool builders
├── validators.py         # Builtin validators (phone, email, …)
├── field_extractors.py   # Opening-message field seeding
├── session.py            # InterviewSession in conversation.context
├── responses.py          # tell_user_directive, interview_tool_response, …
├── decorators.py         # @interview_tool (prefer frontmatter interview.tools:)
├── procedure.py          # Standard SOP load + compose for discover_skill_docs
├── sop/                  # Framework SOP assets (runtime + authoring templates)
├── example/example_interview/   # Reference skill — copy to skills/
├── README.md             # Full reference (frontmatter interview:, tools, patterns)
└── docs/                 # Focused guides (multi-turn, extending, troubleshooting)
```

---

## Creating a new interview skill (minimum steps)

1. Copy [`example/example_interview/`](example/example_interview/) → `skills/<name>/`.
2. Align `name` in folder and `SKILL.md` frontmatter.
3. Implement every `function:` referenced in frontmatter `interview:` inside `scripts/custom_tools.py`.
4. Write `SKILL.md` custom instructions only (standard procedure is injected at discovery — see `sop/skill_custom_instructions.md`).
5. Set `requires-actions: [InterviewAction]` and list tools in `allowed-tools`.
6. Register skill in agent `orchestrator.skills:`.
7. Enable `InterviewAction` in agent actions.

See [README.md](README.md) and [docs/extending.md](docs/extending.md) for validators, hooks, review/completion handlers.

---

## Key invariants

1. **Hook functions are not LLM tools** — only entries in frontmatter `interview.tools` become `{skill}__{name}` tools.
2. **`interview__set_field` uses parameter `field`** — not `name`.
3. **Chaining gate** — read `ok` from every tool response before advancing; `post_tools` do not run when `ok: false`.
4. **`response_directive` beats `next_questions`** when they conflict — one action per turn.
5. **Review before complete** — always call `interview__review()` before `interview__complete()` unless review sets `terminate: true`.
6. **Contract discovery** — `InterviewRegistry` scans `skills/` only; `example/` is not auto-discovered.
7. **Never reuse stale field values** from older chat turns unless the user repeats them in the latest message.

---

## Tests

```bash
pytest tests/action/interview_action/ -v
```

---

## Read next

| Doc | Topic |
|-----|-------|
| [README.md](README.md) | Full contract reference, tool envelope, live skill patterns |
| [docs/README.md](docs/README.md) | Documentation index |
| [docs/multi-turn-flow.md](docs/multi-turn-flow.md) | Turn-by-turn lifecycle, turn-lock, session states |
| [docs/extending.md](docs/extending.md) | Validators, pre/post tools, review/completion, custom tools |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Common failures and fixes |
| [example/example_interview/](example/example_interview/) | Reference implementation |
