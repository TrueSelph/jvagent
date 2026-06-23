# ADR 0027 — Conversation Use Case Specification (CUCS)

**Status**: Accepted
**Date**: 2026-06-23
**Builds on**: [`0026-task-driven-turn-lock.md`](0026-task-driven-turn-lock.md) (task-graph gating/resume semantics scenarios assert against), [`0023-skill-placement-standard.md`](0023-skill-placement-standard.md) (skill paths in traceability)

---

## 1. Context

jvagent apps ship multi-skill conversational flows (interviews, gated services, orchestrator detours) but lack a **portable, stakeholder-readable** format for documenting end-to-end user journeys that inform test suites.

Today:

- **`SKILL.md` `interview:` frontmatter** defines field-level implementation contracts ([`frontmatter-schema.md`](../../jvagent/action/interview/docs/frontmatter-schema.md)).
- **Handler unit tests** exercise hooks and validators imperatively (direct `_handle_*` calls).
- **Orchestrator tests** prove framework mechanics (`test_requires_tasks.py`, `test_example_gated_skill.py`) with domain-neutral Python — no declarative scenario catalog.
- **App docs** (e.g. zoon `interviews.md`, `account-gating.md`) describe architecture in prose without machine-parseable turns or assertions.

Gaps: no cross-skill scenario format, no QA-readable acceptance scripts, no shared vocabulary for orchestrator E2E tests across jvagent apps.

Industry formats (Gherkin/Cucumber, ASSERT) either lack jvagent primitives (`requires-tasks`, task-graph `seed`/`blocked_on`, `interview__*` tools) or target LLM-judge eval pipelines rather than deterministic orchestrator E2E.

---

## 2. Decision

Introduce the **Conversation Use Case Specification (CUCS)** as a jvagent framework contract:

1. **Schema URI**: `jvagent.use-case/v1`
2. **Normative doc**: [`.planning/reference/conversation-use-cases.md`](../reference/conversation-use-cases.md)
3. **JSON Schema**: [`jvagent/schemas/use-case-v1.schema.json`](../../jvagent/schemas/use-case-v1.schema.json)
4. **Per-app scenario files**: `<app-root>/use-cases/**/*.yaml` conforming to the schema
5. **Per-app implementation guide**: `<app-root>/docs/use-cases.md` documents app-specific extension namespaces (fixtures, API assertions) without forking the schema

### Layering

| Artifact | Role |
|----------|------|
| `SKILL.md` `interview:` | Implementation contract (fields, branches) |
| CUCS YAML | Behavioral scenarios (multi-turn, cross-skill) |
| Handler tests | Hook/validator depth |
| CUCS orchestrator runner (phase 2) | Parametrized E2E from YAML |

### Audience split in each turn

- `when` / `then` — product and QA
- `harness` — engineers and test runners (canned `_run_model` decisions)

### Extensions

Apps add namespaces (e.g. `fixtures.stub`, `api.called`) documented locally and evaluated by app test adapters. The framework owns core namespaces: `task_graph`, `context`, `session`, `publish`, `tools_surface`.

---

## 3. Consequences

**Positive**

- Any jvagent app can author scenarios from one spec + schema.
- zoon-ai serves as reference implementation with pilot catalog.
- Domain-neutral witness at `example_account_gating/use-cases/`.
- Clear separation from `interview:` frontmatter — no field-graph duplication.

**Negative / deferred**

- Phase 1 is docs + YAML only; `jvagent/testing/use_case_loader.py` and app runners are phase 2.
- LLM-judge assertions deferred to a future extension profile.
- `harness` blocks require engineer maintenance alongside model behavior changes.

---

## 4. References

- Spec: [conversation-use-cases.md](../reference/conversation-use-cases.md)
- Witness: [`example_account_gating/use-cases/`](../../jvagent/action/interview/examples/example_account_gating/use-cases/)
- Reference implementation: zoon-ai `docs/use-cases.md` + `use-cases/`
