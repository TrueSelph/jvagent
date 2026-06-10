# interview_action — Agent Guide

---

## What this is

`InterviewAction` is a pure `Action` (not `InteractAction`) that registers eight fixed `interview__*` tools plus per-skill custom tools. The **orchestrator LLM** reads each skill's `SKILL.md` procedure and drives multi-turn interviews by calling tools — the action manages session state, validation, hooks, and task tracking; it does **not** classify user intent or choose the next question itself.

Session state lives in `conversation.context["interview"]` as a lightweight `InterviewSession` dataclass (field values, skipped fields, status, scratch `context` dict).

**Design contract:** always build and extend interviews with the **[thin harness principle](../../../docs/thin-harness.md)** (platform) and **[interview profile](docs/thin-harness.md)** — thick SOP + skill extensions, thin server steering.

---

## Foundation vs skill extensions

**`interview_action/` is a reusable foundation.** It must stay domain-agnostic: no signup/training phrases, no per-skill field names, no business validators hardcoded in `interview_action.py`.

| Layer | Location (per consuming app) | Owns |
|-------|------------------------------|------|
| **Foundation** | `jvagent/action/interview_action/` | `interview__*` tools, session lifecycle, hook dispatch, validator *invocation*, runtime-ready turn-lock gate, generic pipeline |
| **Base SOP** | `SKILL.md` (action root) | Inherited via `extends: action:jvagent/interview_action` |
| **Spec** | `skills/<name>/SKILL.md` frontmatter `interview:` | Fields, order, branches, validator names, pre/post processors, handlers, skill_tools |
| **Procedure** | `skills/<name>/SKILL.md` body | Custom behavioral rules only (base composed via `extends`) |
| **Implementation** | `skills/<name>/scripts/custom_tools.py` | Validators, pre/post tools, completion handlers, custom LLM tools |

When fixing behavior for one skill (e.g. `validate_full_name`, training slot matching), change the **skill extension** — not the foundation — unless the bug is in generic plumbing (chaining, validator dispatch, hook dispatch, session persistence).

**Terminal cleanup:** `complete`, `cancel`, and `interview_complete` validators call `clear_interview_context()` — wipes `conversation.context` except platform keys (`new_user`) and any `retain_context_keys` returned by the completion handler or validator. Do not persist interview scratch in `conversation.context` unless opting in via `retain_context_keys`.

---

## File map

```
interview_action/
├── SKILL.md              # Base SOP (extends target)
├── interview_action.py   # Action shell: discovery, turn-lock hooks, skill activation
├── spec.py               # Frontmatter parsing: FieldDef / InterviewSpec / registry
├── session.py            # InterviewSession + conversation persistence
├── flow.py               # Branch evaluation, path walk, prune
├── hooks.py              # custom_tools.py loader, call_hook, validator dispatch
├── validators.py         # Built-in validators
├── engine.py             # The 8 tool handlers + activation + skill-tool dispatch
├── tools.py              # Tool definitions binding to engine
├── responses.py          # Response envelope + directive strings
├── tasks.py              # INTERVIEW task lifecycle
├── procedure.py          # SOP composition
├── _validate_contract.py # Skill frontmatter ↔ custom_tools.py validation
├── info.yaml
├── README.md / CLAUDE.md / AGENTS.md
├── examples/             # Reference skill packages (not auto-discovered)
└── docs/                 # How-to guides + skill_custom_instructions.md
```

---

## Creating a new interview skill (minimum steps)

1. Copy [`examples/example_interview/`](examples/example_interview/) → `agents/<ns>/<agent>/skills/<name>/`.
2. Align `name` in folder and `SKILL.md` frontmatter.
3. Implement every `function:` referenced in frontmatter `interview:` inside `scripts/custom_tools.py`.
4. Write `SKILL.md` custom instructions only; set `extends: action:jvagent/interview_action` (see `docs/skill_custom_instructions.md`).
5. Set `extends: action:jvagent/interview_action` and `requires-actions: [InterviewAction]`. Add custom LLM tools to additive `allowed-tools` only.
6. Register skill in agent `orchestrator.skills:`.
7. Enable `InterviewAction` in agent actions.

See [README.md](README.md) and [docs/extending.md](docs/extending.md) for validators, hooks, review/reset/completion handlers.

---

## Key invariants

Full tables: **[interview profile](docs/thin-harness.md)** (+ [platform](../../../docs/thin-harness.md)). Summary:

1. **Thin harness** — no server intent classification, no prep observations, no activation auto-store, no merge-inlined next/review responses, no `extractors` in frontmatter.
2. **Hook functions are not LLM tools** — only entries in frontmatter `interview.skill_tools` become `{skill}__{name}` tools. Reset uses `handlers.reset` (invoked via `interview__reset()`).
3. **Model owns extraction and chaining** — `interview__set_fields` + base SOP; read `ok` before advancing; post-processors do not run when `ok: false`.
4. **`response_directive` beats `next_fields`** when they conflict — one action per turn.
5. **Review before complete** — always call `interview__review()` before `interview__complete()` unless review sets `terminate: true` or `confirm: auto` chains complete.
6. **Contract discovery** — `InterviewRegistry` scans dirs from `Action.resolve_skill_scan_dirs()` (app `skills/` + action-bundled paths). Author interview skills under `agents/.../skills/<name>/` (ADR-0023). Reference packages live under `examples/` (not discovered).
7. **Never reuse stale field values** from older chat turns unless the user repeats them in the latest message.
8. **Domain logic in skills** — validators, processors, handlers, and branching in `custom_tools.py` + `interview:` frontmatter; never in foundation code.

---

## Tests

```bash
pytest tests/action/interview_action/ -v
```

---

## Read next

| Doc | Topic |
|-----|-------|
| [docs/thin-harness.md](../../../docs/thin-harness.md) | **Thin harness** (jvagent-wide principle) |
| [docs/thin-harness.md](docs/thin-harness.md) | **Interview profile** (subsystem invariants) |
| [docs/frontmatter-schema.md](docs/frontmatter-schema.md) | Canonical `interview:` YAML schema |
| [README.md](README.md) | Reading paths, tool envelope, live skill patterns |
| [docs/multi-turn-flow.md](docs/multi-turn-flow.md) | Turn-by-turn lifecycle, turn-lock, session states |
| [docs/extending.md](docs/extending.md) | Validators, processors, handlers, skill tools |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Common failures and fixes |
| [examples/example_interview/](examples/example_interview/) | Reference implementation |
