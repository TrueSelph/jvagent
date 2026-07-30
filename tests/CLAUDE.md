# tests/ — Agent Guide

> Local guide for the test suite. Cross-link: root [`/CLAUDE.md`](../CLAUDE.md), [`/.planning/runbooks/local-dev.md`](../.planning/runbooks/local-dev.md).

---

## 1. Layout (mirrors `jvagent/`)

```
tests/
├── conftest.py              # session-level fixtures
├── action/                  # per-action unit tests
│   ├── orchestrator/     # Orchestrator loop
│   ├── interact/            # walker bootstrap + visit semantics
│   ├── interview/           # branching, convergence, pruning
│   ├── mcp/
│   ├── model/
│   ├── pageindex/
│   ├── postiz_action/
│   ├── response/
│   ├── task_creation_interact_action/
│   ├── task_monitor/
│   ├── whatsapp/
│   ├── google/
│   ├── facebook_action/
│   ├── email_action/
│   ├── access_control/
│   ├── test_action_loader.py
│   ├── test_action_endpoints.py
│   ├── test_plugin_system.py
│   ├── test_no_persona_imports.py, test_reply*.py
│   ├── test_secrets.py
│   ├── test_vision*.py
│   └── ...
├── core/                    # framework-level tests
├── memory/                  # memory subsystem tests
├── cli/                     # CLI argparse + dispatch
├── scaffold/                # `jvagent app create` flow
├── bundle/                  # bundle/Dockerfile generation
├── skills/                  # skill discovery + dispatch
├── integration/             # end-to-end flows
├── wire/                    # WIRE-CONTRACT tier — see §3.1
├── unit/                    # cross-cutting unit tests
├── test_comprehensive_pruning.py
├── test_pruning_fix.py
├── test_interview_branch_cache.py
├── test_interview_path_pruning_and_convergence.py
├── test_stress_seed_graph.py
├── test_tool_schema_audit.py
├── test_embed.py
└── test_env_load.py
```

---

## 2. Running a slice

```bash
pytest tests/                      # everything (slow)
pytest tests/action/orchestrator/ -v  # Orchestrator
pytest -k pruning                  # by keyword
pytest --lf                        # last-failed only
pytest -x                          # stop on first failure
pytest -n auto                     # parallel (pytest-xdist if installed)
```

---

## 3. Fixtures + conventions

- `pytest-asyncio` is configured for `async def` tests.
- `conftest.py` provides DB context fixtures. Don't re-create one per test.
- Mock external HTTP via `pytest-httpx` or `respx`.
- For walker-level tests, construct an `InteractWalker` directly; see `tests/action/interact/` for patterns.
- For full integration, see `tests/integration/`.
- For stress / synthetic data: `tests/test_stress_seed_graph.py` + the `stress-seed` CLI subcommand.

### 3.1 The wire tier (`tests/wire/`)

Everything else in this suite constructs actions in memory. `tests/wire/` does
not: it bootstraps a real app graph from YAML, loads the action **back out of
the database**, and asserts on the exact prompt a tick would send. Only the
model is stubbed.

Use it when the thing that can break is *wiring* rather than logic:

| Symptom it catches | Why unit tests miss it |
|---|---|
| A stale persisted attribute beating a new code default | The constructed object has the new default |
| A render site that stopped calling its helper | The helper's own test still passes |
| A rule that renders twice, or not at all | Neither is visible without the assembled prompt |
| Interpreter-order dependence reaching the model | Needs two processes with different `PYTHONHASHSEED` |

```bash
pytest tests/wire/ -q          # ~5s, boots a real graph per test
```

Conventions:
- **Assert on captured wire text, never on a helper's return value.** A helper
  can be correct while the code that calls it is not — that is the failure mode
  this tier exists for.
- The `wire` fixture is function-scoped on purpose. jvspatial objects bind to
  the event loop that created them; a session-scoped graph fails across
  pytest-asyncio's per-test loops.
- Cross-process checks go through `tests/wire/_render_once.py` as a subprocess
  so the caller controls `PYTHONHASHSEED`.
- **Mutation-check new tests here.** Break the behaviour on purpose and confirm
  the test goes red before trusting it.

---

## 4. When you add a feature

Add at least one test slice:

| Touched | Add tests at |
|---|---|
| `core/` | `tests/core/` |
| `memory/` | `tests/memory/` + regression in `tests/test_comprehensive_pruning.py` if it affects pruning |
| `action/{name}/` | `tests/action/{name}/` |
| `action/interact/` | `tests/action/interact/` + `tests/action/access_control/` if access control changes |
| `action/orchestrator/` | `tests/action/orchestrator/` |
| Prompt assembly, parameter rendering, persisted config | `tests/wire/` as well — logic tests do not see wiring |
| `cli/` | `tests/cli/` |
| Tool schemas | check `tests/test_tool_schema_audit.py` still passes; add cases |

For pure-doc PRs, no tests are required, but `pre-commit run --all-files` still runs.

---

## 5. Contracts

1. **Tests must not depend on a running MongoDB unless explicitly marked.** Use the JSON backend (`JVSPATIAL_DB_TYPE=json` in `conftest.py`).
2. **Tests must clean up after themselves.** Use the fixture-managed DB context; do not write to the production `jvdb/`.
3. **No real network calls.** Mock the HTTP layer.
4. **Async tests must use `@pytest.mark.asyncio` and `async def`.**
5. **Test names start with `test_`**; helper files are `_helpers.py` or `conftest.py`.

---

## 6. Traps specific to tests/

| Trap | Fix |
|---|---|
| Tests pass alone but fail together | DB context leaks between tests. Use the per-test fixture from `conftest.py`. |
| Walker tests time out | Default `max_execution_time=300` — set lower in test setup if needed. |
| Mocking jvspatial entities directly | Brittle. Mock at the HTTP / model boundary instead. |
| Hard-coding action IDs in tests | Use the fixture that creates the action and returns its ID. |
| Skipping the commit gate | `pre-commit run --all-files` + `pytest` must pass before **every** commit (root [`CLAUDE.md` §6](../CLAUDE.md)). Never `--no-verify`. |
| Asserting on log strings | Use `caplog` fixture, not string match on stderr. |

---

## 7. Don't touch from outside tests/

- `conftest.py` — shared fixtures with order constraints.
- Stress-seed scenarios — they drive `tests/test_stress_seed_graph.py` and a CLI subcommand simultaneously.
