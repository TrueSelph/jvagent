# Conversation Use Case Specification (CUCS)

> **Contract for documenting multi-turn conversational scenarios** that inform orchestrator E2E test suites. This is the single source of truth for the `jvagent.use-case/v1` schema. Apps (e.g. zoon-ai) author scenario YAML under their own `use-cases/` tree and document app-specific extensions in a local implementation guide — they do not fork the schema.

Cross-refs: [`adr/0027-conversation-use-case-spec.md`](../adr/0027-conversation-use-case-spec.md), [`frontmatter-schema.md`](../../jvagent/action/interview/docs/frontmatter-schema.md) (interview field contract — different layer), [`test_requires_tasks.py`](../../tests/action/orchestrator/test_requires_tasks.py) (task-graph semantics), JSON Schema at [`jvagent/schemas/use-case-v1.schema.json`](../../jvagent/schemas/use-case-v1.schema.json).

---

## 1. What CUCS is (and is not)

| Layer | Owns | Example |
|-------|------|---------|
| **`SKILL.md` `interview:` frontmatter** | Field order, branches, validators, handlers | `account_provisioning` OTP branch |
| **CUCS scenario YAML** | Cross-skill user journeys, orchestrator detours, multi-turn outcomes | Gated quotation → provisioning → resume |
| **Handler / hook unit tests** | Validator and processor depth | `test_account_provisioning.py` |

CUCS uses **Given-When-Then vocabulary** (BDD-inspired) but is **not** Gherkin — it encodes jvagent primitives (`requires-tasks`, task-graph `blocked_on`/`seed`, `use_skill`, `interview__*` tools) directly.

---

## 2. File layout (per app)

```
<app-root>/
  use-cases/
    <domain>/
      <scenario-id>.yaml    # one use case per file
    stubs/                  # optional — app fixture profiles (extension namespace)
  docs/
    use-cases.md            # implementation guide (links here + documents extensions)
```

jvagent ships a **domain-neutral witness** at [`jvagent/action/interview/examples/example_account_gating/use-cases/`](../../jvagent/action/interview/examples/example_account_gating/use-cases/).

---

## 3. Schema URI

Every scenario file MUST declare:

```yaml
schema: jvagent.use-case/v1
```

Validate with [`jvagent/schemas/use-case-v1.schema.json`](../../jvagent/schemas/use-case-v1.schema.json).

---

## 4. Top-level fields

| Field | Required | Description |
|-------|----------|-------------|
| `schema` | Yes | Must be `jvagent.use-case/v1` |
| `id` | Yes | Stable dot-separated identifier (e.g. `gating.quotation.new-user`) |
| `title` | Yes | Human-readable one-line summary |
| `priority` | No | `P0` \| `P1` \| `P2` — test triage |
| `tags` | No | Free-form labels for filtering |
| `app` | No | Consuming app id (from `app.yaml`) |
| `traceability` | No | Links to skills and docs — not duplicated field graphs |
| `given` | Yes | World state before turn 1 |
| `turns` | Yes | Ordered list of user turns |
| `outcome` | No | Terminal assertions after all turns |

### `traceability`

```yaml
traceability:
  skills: [quotation_interview, account_provisioning]
  docs:
    - docs/account-gating.md
    - docs/interviews.md
```

Reference `SKILL.md` and architecture docs; do **not** copy `interview:` field definitions into scenarios.

---

## 5. `given` — initial world state

```yaml
given:
  channel: web              # web | whatsapp | default
  new_user: false
  context: {}               # pre-seed conversation.context
  preconditions:            # boolean overrides for register_precondition() names
    account_session: false
  fixtures: {}              # app extension namespace — see §8
```

| Key | Description |
|-----|-------------|
| `channel` | Visitor channel passed to orchestrator tests |
| `new_user` | Sets `visitor.new_user` when true |
| `context` | Merged into `conversation.context` before turn 1 |
| `preconditions` | Map of precondition name → expected boolean for test setup (app registers evaluators) |
| `fixtures` | Opaque-to-framework fixture references; apps document namespaces locally |

---

## 6. `turns` — When / harness / Then

Each turn is one orchestrator `execute()` cycle unless `harness.decisions` contains multiple entries consumed in one cycle.

```yaml
turns:
  - id: request-quote
    when:
      user: "Can I get a quote for https://example.com/product"
    harness:
      decisions:
        - action: tool
          tool: use_skill
          args:
            skill: quotation_interview
    then:
      task_graph:
        pushed: account_provisioning
        blocked:
          quotation_interview: account_provisioning
        seed:
          quotation_interview:
            utterance: "Can I get a quote for https://example.com/product"
        runnable: account_provisioning
```

### Audience split

| Block | Primary reader | Purpose |
|-------|----------------|---------|
| `when` | Product / QA | What the user says or does |
| `then` | Product / QA + test runner | Observable system outcomes |
| `harness` | Engineers + test runner | Canned `_run_model` returns for deterministic orchestrator E2E |

Product reviewers can read `when`/`then` without `harness`. Engineers implement test runners from `harness`.

### `when`

| Key | Description |
|-----|-------------|
| `user` | User utterance for this turn (`visitor.utterance`) |

### `harness.decisions`

List of mocked LLM decisions, consumed in order per orchestrator `execute()`. Each decision:

| `action` | Shape |
|----------|-------|
| `tool` | `{ action: tool, tool: <name>, args: {…} }` |
| `final` | `{ action: final, answer: "<text>" }` |

Maps to jvagent test helper `make_orchestrator(decisions=[…])` ([`tests/action/orchestrator/conftest.py`](../../tests/action/orchestrator/conftest.py)).

### `then` — core assertion namespaces

All keys under `then` are optional per turn. See §7 for semantics.

---

## 7. Assertion vocabulary (framework-owned)

These namespaces are evaluated by the jvagent use-case loader (phase 2). Apps MUST NOT redefine them.

### `task_graph`

| Key | Type | Semantics |
|-----|------|-----------|
| `pushed` | string | Name of prerequisite skill pushed by `push_unmet_prerequisites` |
| `blocked` | map | `{ parent_skill: blocker_skill }` — parent task `blocked_on` blocker |
| `seed` | map | `{ skill_name: { utterance?, fields? } }` — seeded request on gated task |
| `runnable` | string | Skill name of `pick_top_runnable` top SKILL task |
| `not_pushed` | string | Assert no prerequisite was pushed |

Code anchors: [`skill_tasks.py`](../../jvagent/action/orchestrator/skill_tasks.py), [`test_requires_tasks.py`](../../tests/action/orchestrator/test_requires_tasks.py), ADR-0026.

### `context`

Dot-path keys or nested objects merged into `conversation.context` expectations.

```yaml
then:
  context:
    interview.status: active
    zoon_account:
      required_keys: [account_number, phone_number, email]
```

### `session`

Active `InterviewSession` (from `conversation.context["interview"]`).

```yaml
then:
  session:
    interview_type: account_provisioning
    fields:
      email: user@example.com
    context:
      duplicate_outcome: both_match
```

### `publish`

User-visible reply text from orchestrator `publish`.

```yaml
then:
  publish:
    contains: ["verification code"]
    matches: ".*quote.*"    # optional regex
```

### `tools_surface`

Tools exposed to the model on a specific `_run_model` call (0-based index).

```yaml
then:
  tools_surface:
    call_index: 0
    includes: [use_skill, interview__set_fields, interview__next_field]
```

---

## 8. Extension namespaces (app-owned)

Apps extend CUCS without changing `jvagent.use-case/v1`. Document allowed keys in the app's `docs/use-cases.md`. Register adapters in the app's test runner.

| Pattern | Example (zoon-ai) | Declared in |
|---------|-------------------|-------------|
| `given.fixtures.<ns>` | `fixtures.stub: profile/default-unregistered` | `zoon-ai/docs/use-cases.md` |
| `then.api` | `api.called: [request_email_otp]` | `zoon-ai/docs/use-cases.md` |

**Rule:** Extension keys live under `fixtures` (given) or top-level `then` namespaces not listed in §7. The JSON Schema allows additional properties on `then` and `given.fixtures` for this purpose.

---

## 9. `outcome` — terminal state

Optional block asserting end state after all turns complete. Uses the same namespaces as `then`.

```yaml
outcome:
  task_graph:
    runnable: quotation_interview
  context:
    zoon_account:
      required_keys: [account_number, phone_number, email]
```

---

## 10. Authoring rules

1. **One file per use case** — stable `id` matches filename stem where practical.
2. **Do not duplicate `interview:` field graphs** — link via `traceability.skills`.
3. **Prefer core namespaces** over app extensions when the assertion is orchestrator-generic.
4. **Keep turns focused** — one user message per turn; multi-tool chains go in `harness.decisions`.
5. **Name preconditions** exactly as registered via `register_precondition()` in the app.
6. **Stub / fixture profiles** are reusable YAML under `use-cases/stubs/` referenced from `given.fixtures`.

---

## 11. Worked example (domain-neutral)

See [`jvagent/action/interview/examples/example_account_gating/use-cases/booking-gated-no-session.yaml`](../../jvagent/action/interview/examples/example_account_gating/use-cases/booking-gated-no-session.yaml).

---

## 12. Phase 2 — test runner

jvagent will ship `jvagent/testing/use_case_loader.py` to:

1. Load and validate YAML against the JSON Schema
2. Evaluate §7 assertion namespaces
3. Accept app-registered fixture and extension evaluators

Apps wire orchestrator fixtures (see zoon-ai `tests/use_cases/` when added).

---

## 13. Out of scope (v1)

- LLM-judge / semantic similarity assertions (future `profile: llm-eval` extension)
- Live model-in-the-loop runs (scenarios include `harness` for mocked orchestrator only)
- Handler-internal hook assertions (remain in unit tests)
