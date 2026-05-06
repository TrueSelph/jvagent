# Testing Patterns

**Analysis Date:** 2026-05-06

## Test Framework

**Runner:**
- `pytest >=7.0` (`minversion = "7.0"` enforced in `pyproject.toml`)
- `pytest-asyncio >=0.21.0` for async test support
- `pytest-cov >=4.0.0` for coverage
- `coverage >=7.0.0` for reporting
- Config: `pyproject.toml` `[tool.pytest.ini_options]`

**Pytest Configuration (`pyproject.toml`):**
```toml
[tool.pytest.ini_options]
minversion = "7.0"
addopts = "-ra -q --strict-markers"
testpaths = ["tests"]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
markers = [
    "asyncio: mark test as an asyncio test",
]
filterwarnings = [
    "ignore::DeprecationWarning:pydantic.*",
    "ignore::PendingDeprecationWarning",
    "ignore::DeprecationWarning:starlette.*",
    "ignore:builtin type.*has no __module__:DeprecationWarning",
    "ignore:enable_cleanup_closed ignored:DeprecationWarning:aiohttp.*",
]
```

**Run Commands:**
```bash
pytest tests/                                  # Run full suite
pytest tests/ -v --tb=short                    # CI invocation (verbose, short tracebacks)
pytest tests/path/to/test_file.py              # Run single file
pytest tests/path/to/test_file.py::TestClass   # Run a single class
pytest --cov=jvagent --cov-report=term         # With coverage
pre-commit run --hook-stage manual pytest      # Run via the manual pre-commit hook
```

**Run via CI:** `pytest tests/ -v --tb=short` followed by `pre-commit run --all-files` in `.github/workflows/test-jvagent.yaml`.

## Test File Organization

**Location:**
- Tests live in a top-level `tests/` directory (not co-located with source)
- Mirrors source structure: `tests/action/skill/` mirrors `jvagent/action/skill/`, `tests/memory/` mirrors `jvagent/memory/`, `tests/core/` mirrors `jvagent/core/`
- 151 test files totalling ~29,210 lines of test code (vs ~118,770 lines of source)

**Naming:**
- Files prefixed with `test_` (e.g., `test_skill_action_core.py`, `test_pruning_fix.py`)
- Test classes prefixed with `Test` (e.g., `TestSkillRunConfig`, `TestTaskStoreCreate`)
- Test functions prefixed with `test_` (e.g., `test_create_creates_active_task`, `test_pruning_removes_unreachable_responses`)

**Directory Layout:**
```
tests/
├── __init__.py                                    # "Test suite for jvagent."
├── conftest.py                                    # Single shared conftest (root only)
├── test_*.py                                      # Top-level cross-cutting tests
├── action/                                        # Mirrors jvagent/action/
│   ├── __init__.py
│   ├── test_action_loader.py
│   ├── test_persona_*.py
│   ├── access_control/
│   ├── agent_interact/
│   ├── email_action/
│   ├── facebook_action/
│   ├── interact/
│   ├── interview/
│   ├── long_memory/
│   ├── mcp/
│   ├── model/                                     # LM provider integrations
│   │   └── language/                              # OpenAI / Anthropic / Ollama / OpenRouter retry tests
│   ├── pageindex/
│   ├── postiz_action/
│   ├── response/
│   ├── router/
│   ├── skill/                                     # Largest cluster (~25 files)
│   ├── task_creation_interact_action/
│   ├── task_dispatcher/
│   └── whatsapp/
├── bundle/                                        # Dockerfile generator tests
├── cli/                                           # CLI entry point tests
├── core/                                          # Mirrors jvagent/core/
├── integration/                                   # Live-style smoke tests (test_startup_health.py)
├── memory/                                        # Mirrors jvagent/memory/
│   └── services/
└── scaffold/                                      # Scaffolding & profile resolution
```

**Notable subdirectories with `__init__.py`** (treated as packages):
`tests/action/`, `tests/action/access_control/`, `tests/action/email_action/`, `tests/action/facebook_action/`, `tests/action/interact/`, `tests/action/interview/`, `tests/action/long_memory/`, `tests/action/model/`, `tests/action/pageindex/`, `tests/action/response/`, `tests/action/skill/`, `tests/action/whatsapp/`, `tests/core/`, `tests/memory/`.

## Test Structure

**Module Header:**
```python
"""Tests for SkillAction core, contracts, checkpoint/recovery, compactor, evidence log."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.skill.skill_action import SkillAction
```
(`tests/action/skill/test_skill_action_core.py`)

**Suite Organization:**
- Tests are grouped into `class Test*` blocks separated by section banners:
```python
# ---------------------------------------------------------------------------
# SkillRunConfig defaults
# ---------------------------------------------------------------------------


class TestSkillRunConfig:
    def test_defaults_sane(self):
        cfg = SkillRunConfig()
        assert cfg.max_iterations == 25
        assert cfg.strict_grounding is True
```
- Top-level free functions also used for cross-cutting scenarios (`tests/test_pruning_fix.py`, `tests/test_env_load.py`, `tests/core/test_startup.py`)

**Async Tests:**
- `asyncio_mode = "auto"` means async functions are treated as coroutines automatically
- Despite `auto` mode, the codebase still routinely marks async tests explicitly with `@pytest.mark.asyncio` (~592 occurrences) for clarity:
```python
@pytest.mark.asyncio
async def test_run_app_startup_returns_false_when_actions_fail():
    startup._startup_completed = False
    app = SimpleNamespace(
        initialize_actions=AsyncMock(return_value={"A": True, "B": False})
    )
    with patch("jvagent.core.app.App.get", AsyncMock(return_value=app)):
        result = await startup.run_app_startup()
    assert result is False
```
(`tests/core/test_startup.py`)

**Assertion Style:**
- Plain `assert` statements (no custom assertion library)
- Multi-line assertions broken across lines for readability
- Both positive and negative assertions enforced (`assert ...`, `assert not ...`, `assert X is None`)

## Fixtures

**Shared Conftest (`tests/conftest.py`):**
- Single root-level `conftest.py`; no per-subdirectory conftests
- Three shared fixtures:
  ```python
  @pytest.fixture(autouse=True)
  def _clear_jvspatial_load_env_cache():
      """Shared test setup hook (kept for fixture compatibility)."""
      yield

  @pytest.fixture
  def temp_dir():
      """Create a temporary directory for test files."""
      with tempfile.TemporaryDirectory() as tmpdir:
          yield Path(tmpdir)

  @pytest.fixture(scope="function")
  async def test_db(temp_dir, monkeypatch):
      """Initialize test database and GraphContext."""
      monkeypatch.setenv("JVSPATIAL_ENABLE_DEFERRED_SAVES", "false")
      test_db_path = temp_dir / "test_jvdb"
      test_db_path.mkdir()
      db = JsonDB(base_path=str(test_db_path))
      ctx = GraphContext(database=db)
      set_default_context(ctx)
      yield test_db_path
  ```

**Per-File Fixtures:**
- Tests define module-private factories prefixed with `_make_` (e.g., `_make_conversation`, `_make_task_handle`, `_make_task_store`, `_make_model_result` in `tests/action/skill/test_skill_action_core.py`)
- These return preconfigured `MagicMock` / `AsyncMock` objects with the contracts expected by the unit under test
- `@pytest.fixture` and `@pytest.fixture(autouse=True)` used selectively in test modules (~43 occurrences) for setup that is hard to express as a factory

## Mocking

**Framework:** `unittest.mock` from the standard library — `MagicMock`, `AsyncMock`, `patch` (~75 import sites; ~1,677 mock-related references across the suite).

**Patterns:**
1. **AsyncMock for coroutine-returning APIs:**
   ```python
   th.add_event = AsyncMock(return_value=True)
   th.complete = AsyncMock(return_value=True)
   ```
2. **MagicMock with attribute pre-population:**
   ```python
   conv = MagicMock()
   conv.context = {}
   conv.tasks = []
   conv.save = AsyncMock()
   ```
3. **Async context manager mocking:**
   ```python
   def _tracking_ctx():
       ctx = MagicMock()
       handle = _make_task_handle()
       ctx.__aenter__ = AsyncMock(return_value=handle)
       ctx.__aexit__ = AsyncMock(return_value=False)
       return ctx
   ```
4. **Patch context for module-level callables:**
   ```python
   with patch("jvagent.core.app.App.get", AsyncMock(return_value=app)):
       result = await startup.run_app_startup()
   ```
5. **Async generator helpers:**
   ```python
   async def _aiter(items):
       for item in items:
           yield item
   ```
6. **`monkeypatch` fixture for environment + attribute mutation:** preferred over manual `os.environ` manipulation. Used pervasively in `tests/test_env_load.py` and `tests/conftest.py`.

**What to Mock:**
- External SDKs and HTTP clients (OpenAI, Anthropic, Ollama, OpenRouter providers — `tests/action/model/`)
- Network IO (`httpx`, `aiohttp`)
- The graph database when isolating a unit (`MagicMock` `Conversation`, `User` for `TaskStore`/`SkillAction` tests)
- Module-level singletons via `patch("module.path.symbol")` (e.g., `patch("jvagent.core.app.App.get", ...)`)

**What NOT to Mock:**
- The real `jvspatial` graph store when running scenarios involving traversal (`test_db` fixture provisions a real `JsonDB`)
- Pure data transforms (`SkillAction._reorder_task_calls_dependency_first` is exercised with real lists/dicts)
- YAML loading (validated against actual fixture YAML files in `examples/jvagent_app`)

## Real-DB Tests

**`test_db` fixture (`tests/conftest.py`):**
- Creates a temporary directory + `JsonDB` instance
- Wraps it in a `GraphContext` and sets it as default via `set_default_context(ctx)`
- Forces `JVSPATIAL_ENABLE_DEFERRED_SAVES=false` so `Interaction`/`Conversation` writes flush immediately (deferred saves break tests that re-load entities through `Interaction.get()`)
- Cleanup is automatic via `tempfile.TemporaryDirectory` context

**Tests that use `test_db`:** memory/conversation/interaction tests (`tests/memory/test_*.py`), interview branch and pruning tests (`tests/test_pruning_fix.py`, `tests/action/interview/test_*.py`), graph repair (`tests/core/test_graph_repair*.py`).

## Test Data and Fixtures

**Inline Construction:**
- Most test data is constructed in-place inside the test or in a `_make_*` helper at module scope
- Domain objects often built via dict literals matching the production schema:
```python
session.question_graph = [
    {
        "name": "q1",
        "question": "What is your choice?",
        "constraints": {"type": "string"},
        "branches": [...],
        "default_next": "REVIEW",
    },
    ...
]
```

**No Centralized Fixture Library:**
- No `tests/fixtures/` directory; no separate factory module shared across suites
- Each test file builds the minimal mock graph it needs

**Example Apps:**
- `examples/jvagent_app/` referenced by CI's `python -m jvagent.cli validate examples/jvagent_app` step
- Used as ground-truth YAML for validator tests in `tests/core/test_agent_yaml_validator.py`, `tests/core/test_app_yaml_validator.py`

## Test Categories

**Unit Tests:**
- The bulk of the suite — exercise individual classes in isolation with mocked collaborators
- Examples: `tests/action/skill/test_skill_action_core.py` (helpers, contracts, checkpoint, recovery, evidence log), `tests/memory/test_task_service_typed.py` (TaskStore), `tests/core/test_startup.py`

**Component / Behavior Tests:**
- Use `test_db` to exercise multi-class flows against a real `JsonDB`
- Examples: `tests/test_pruning_fix.py`, `tests/action/interview/test_branching.py`, `tests/core/test_graph_repair.py`

**Integration Tests:**
- `tests/integration/test_startup_health.py` — boots the app and exercises health endpoints
- `tests/action/agent_interact/test_agent_interact_integration.py` — agent-interact pipeline end-to-end

**Validator Tests:**
- `tests/core/test_agent_yaml_validator.py`, `tests/core/test_app_yaml_validator.py`, `tests/core/test_validate_command.py` — verify YAML schema and CLI `validate` command

**Provider/Adapter Tests:**
- `tests/action/model/test_openai_actions.py`, `test_anthropic_actions.py`, `test_ollama_actions.py`, `test_openrouter_actions.py`, `test_streaming_tool_calls.py`, `test_multimodal.py`
- `tests/action/model/language/test_lm_retry.py` — retry semantics for `BaseModelAction` / `LanguageModelAction`

**CLI / Bundle Tests:**
- `tests/cli/test_server_config_env_alignment.py`
- `tests/bundle/test_dockerfile_generator.py`

## Coverage

**Configuration (`pyproject.toml`):**
```toml
[tool.coverage.run]
source = ["jvagent"]
omit = ["*/tests/*"]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "raise AssertionError",
    "raise NotImplementedError",
    "if __name__ == .__main__.:",
]
```

**Tooling:**
- `pytest-cov >=4.0.0` and `coverage >=7.0.0` declared in both `[project.optional-dependencies].dev` and `[project.optional-dependencies].test`

**Enforcement:**
- No coverage threshold gate in CI — coverage is opt-in for local runs (`pytest --cov=jvagent`)

## Common Patterns

**Async Testing:**
```python
@pytest.mark.asyncio
async def test_create_creates_active_task():
    store, conv = _make_store()
    handle = await store.create(title="test task", description="test task")
    await handle.start()
    assert handle.id
    assert len(conv.tasks) == 1
    task = conv.tasks[0]
    assert task["status"] == "active"
```
(`tests/memory/test_task_service_typed.py`)

**Patching Class Methods:**
```python
with patch("jvagent.core.app.App.get", AsyncMock(return_value=app)):
    result = await startup.run_app_startup()
```

**Environment Manipulation:**
```python
def test_env_bool_parsing(monkeypatch):
    from jvspatial.env import env, parse_bool
    monkeypatch.setenv("FEATURE_FLAG", "on")
    assert env("FEATURE_FLAG", default=False, parse=parse_bool) is True
```
(`tests/test_env_load.py`)

**Parametrize / Multiple Cases:**
- Parametrize is supported (markers + decorators counted in 633 places) but most tests prefer one explicit `test_*` per case for readability
- Test classes group related cases (e.g., `TestRecoveryPolicy.test_non_recoverable_returns_terminate`, `test_recoverable_within_budget_returns_retry`, `test_budget_exhaustion_returns_terminate`)

**Error / Branch Testing:**
```python
def test_apply_plan_first_tool_gate_blocks_substantive_without_plan(self):
    d, syn, blocked = SkillAction._apply_plan_first_tool_gate(...)
    assert d == []
    assert len(syn) == 1
    assert "mcp_x" in blocked
```

**Round-Trip Testing for Serialization:**
```python
def test_round_trip(self):
    ckpt = LoopCheckpoint(iteration=3, phase="model_call", elapsed_seconds=12.5, ...)
    d = ckpt.to_dict()
    restored = LoopCheckpoint.from_dict(d)
    assert restored.iteration == 3
```
(`tests/action/skill/test_skill_action_core.py`)

## Warning Suppression

The suite explicitly silences known-noisy deprecation warnings to keep test output focused:
- `DeprecationWarning:pydantic.*` (Pydantic v1 → v2 transitional warnings)
- `PendingDeprecationWarning`
- `DeprecationWarning:starlette.*`
- `DeprecationWarning:aiohttp.*` (the `enable_cleanup_closed ignored` message)
- A specific message about `builtin type.*has no __module__`

If you see these warnings come back, check `[tool.pytest.ini_options].filterwarnings` in `pyproject.toml`.

## Adding New Tests

1. **Place the test file** mirroring the source path:
   - Source `jvagent/action/foo/bar.py` → test `tests/action/foo/test_bar.py`
2. **Add `__init__.py`** if creating a new package directory
3. **Open with a docstring** describing the area under test (`"""Tests for ..."""`)
4. **Import order:** `__future__`, stdlib, third-party (`pytest`, `unittest.mock`), then `jvagent.*`
5. **Group cases under `class Test*`** for related scenarios; otherwise use top-level `test_*` functions
6. **Use `@pytest.mark.asyncio`** even though `asyncio_mode = "auto"` is set — this is the project convention
7. **Use `MagicMock` / `AsyncMock`** for collaborators; reach for `test_db` only when the test must exercise real graph traversal
8. **Keep mock factories module-private** (`_make_*`) and place them above the test classes
9. **Assert against the public contract** — observable state, return values, mock call assertions (`mock.save.assert_called()`)

---

*Testing analysis: 2026-05-06*
