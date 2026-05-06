# Testing Patterns

**Analysis Date:** 2026-05-06

## Test Framework

**Runner:**
- pytest 7.0+ (minimum version)
- pytest-asyncio 0.21.0+ for async test support
- Config: `pyproject.toml` [tool.pytest.ini_options]

**Assertion Library:**
- pytest's native assertions (no external library)
- Simple assert statements: `assert result is False`, `assert result.get("status") == "completed"`
- Dictionary assertion patterns: `assert "key" in result`, `assert result["field"] == expected_value`

**Run Commands:**
```bash
pytest tests/                          # Run all tests
pytest tests/ -v                       # Verbose output with test names
pytest tests/ --tb=short              # Short traceback format
pytest -k "test_name"                 # Run specific test by name
pytest tests/core/                    # Run tests in subdirectory
pytest -m asyncio                     # Run only asyncio-marked tests
pytest --cov=jvagent --cov-report=html  # Generate coverage report
```

**Configuration Details:**
```ini
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
]
```

## Test File Organization

**Location:**
- Tests co-located with source in parallel structure
- `tests/` directory mirrors `jvagent/` structure
- Example: `jvagent/core/` has corresponding `tests/core/`

**Directory Structure:**
```
tests/
├── conftest.py                          # Shared fixtures
├── test_env_load.py                     # Root-level tests
├── test_interview_branch_cache.py
├── test_comprehensive_pruning.py
├── test_tool_schema_audit.py
├── core/
│   ├── __init__.py
│   ├── test_startup.py
│   ├── test_graph_repair.py
│   ├── test_callback.py
│   ├── test_agent_yaml_validator.py
│   ├── test_config_env_coercion.py
│   └── ... (24 test files)
├── action/
│   └── ... (action-specific tests)
└── memory/
    └── ... (memory-specific tests)
```

**Naming:**
- Test files: `test_*.py`
- Test functions: `def test_*()`
- Test classes: `class Test*:` (optional grouping)
- Private test helpers: `def _helper_name()`

## Test Structure

**Module-level docstring:**
```python
"""Tests for agent graph repair utility."""
```

**Test class pattern** (grouping related tests):
```python
class TestGraphRepair:
    """Test graph repair functionality."""

    @pytest.mark.asyncio
    async def test_repair_returns_expected_structure(self, temp_dir, test_db):
        """Repair returns dict with all expected keys including memory repair fields."""
        await Root.get()

        result = await _repair_to_completion(dry_run=False)

        assert "memory_repair_agents" in result
        assert "orphaned_interactions_deleted" in result
        # ... more assertions
```

**Function-level tests** (standalone functions):
```python
@pytest.mark.asyncio
async def test_operator_condition_reevaluates_on_session_state():
    """Evaluator re-runs each time; changing response changes result."""
    session = InterviewSession()
    session.interview_type = "TestInterview"
    question_name = "q1"
    
    result1 = await QuestionBranchEvaluator.matches(condition, session)
    assert result1 is True
    
    session.responses[question_name] = "no"
    result2 = await QuestionBranchEvaluator.matches(condition, session)
    assert result2 is False
```

**Test function pattern:**
1. **Setup phase**: Create test data, initialize objects
2. **Action phase**: Call the function/method being tested
3. **Assert phase**: Verify results with explicit assertions
4. **Docstring**: One-line description of what is being tested

## Fixtures

**Shared fixtures in `conftest.py`:**

```python
@pytest.fixture(autouse=True)
def _clear_jvspatial_load_env_cache():
    """Shared test setup hook (kept for fixture compatibility)."""
    yield
```

**Temporary directory fixture:**
```python
@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)
```

**Test database fixture** (async, function-scoped):
```python
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
    # Cleanup by tempfile.TemporaryDirectory
```

**Fixture usage:**
```python
async def test_repair_on_clean_installed_agent(self, temp_dir, test_db):
    """Tests can inject temp_dir and test_db."""
    agent_dir = temp_dir / "agents" / "ns" / "agent1"
    # Use fixtures...
```

**monkeypatch fixture:**
- Built-in pytest fixture for environment variable and attribute patching
- Used extensively for environment variable testing
- Example: `monkeypatch.setenv("PRIMARY_KEY", "primary")`

## Parametrized Tests

**Pattern using @pytest.mark.parametrize:**
```python
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("0", False),
        ("no", False),
        ("off", False),
        ("maybe", None),
        ("", None),
    ],
)
def test_parse_env_bool(raw, expected):
    assert parse_env_bool(raw) == expected
```

**Benefits:**
- Single test function runs multiple times with different inputs
- Clear documentation of expected behaviors
- Easy to add new cases

## Mocking

**Framework:** unittest.mock

**Patterns:**

```python
from unittest.mock import AsyncMock, patch

# Mock async functions
async def test_run_app_startup_returns_false_when_actions_fail():
    app = SimpleNamespace(
        initialize_actions=AsyncMock(return_value={"A": True, "B": False})
    )
    
    with patch("jvagent.core.app.App.get", AsyncMock(return_value=app)):
        result = await startup.run_app_startup()
    
    assert result is False
```

**What to Mock:**
- External service calls (APIs, databases)
- AsyncMock for async dependencies
- patch() context manager for temporary patches
- SimpleNamespace for creating stub objects with attributes

**What NOT to Mock:**
- The core business logic being tested
- Database operations when using test_db fixture
- Graph traversal logic (use real objects)
- Helper functions in the same module

**Mock verification:**
```python
async_mock = AsyncMock(return_value=expected_result)
# After calling function...
# async_mock.assert_called_once()
# async_mock.assert_called_with(arg1, arg2)
```

## Test Data and Factories

**Inline test data:**
```python
session = InterviewSession()
session.interview_type = "TestInterview"
session.question_name = "q1"
session.responses[question_name] = "yes"

dead_edge = _dead_edge_data(
    "e.Edge.dead_edge_test",
    "n.Node.nonexistent_source",
    "n.Node.nonexistent_target",
)
```

**Test helper functions:**
```python
def _dead_edge_data(edge_id: str, source: str, target: str) -> dict:
    """Build edge data in persistence format (includes context for deserialization)."""
    return {
        "id": edge_id,
        "entity": "Edge",
        "type_code": "e",
        "context": {},
        "source": source,
        "target": target,
        "bidirectional": True,
    }

async def _repair_to_completion(**kwargs: Any) -> Dict[str, Any]:
    """Synchronous engine: re-invoke until the pipeline reports completed."""
    last: Dict[str, Any] = {}
    for _ in range(_REPAIR_MAX_STEPS):
        last = await repair_agent_graph(**kwargs)
        if last.get("status") == "completed":
            return last
    raise AssertionError("repair did not complete within %d steps" % _REPAIR_MAX_STEPS)
```

**File system fixtures:**
```python
agent_dir = temp_dir / "agents" / "ns" / "agent1"
agent_dir.mkdir(parents=True)
(agent_dir / "agent.yaml").write_text(
    """agent: ns/agent1
version: 1.0.0
author: Test
"""
)
```

## Coverage

**Configuration in pyproject.toml:**
```ini
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

**Generate coverage report:**
```bash
pytest --cov=jvagent tests/
pytest --cov=jvagent --cov-report=html tests/
pytest --cov=jvagent --cov-report=term-missing tests/
```

**Requirements:** No coverage percentage enforced (not in CI/CD)

**Excluded from coverage:**
- `__repr__` methods
- AssertionError/NotImplementedError raises
- `if __name__ == "__main__"` guards
- Lines marked with `# pragma: no cover`

## Test Types

**Unit Tests:**
- Scope: Individual functions and methods
- Isolation: Mock external dependencies
- Examples:
  - `test_env_single_key_read`: Tests env variable reading
  - `test_parse_env_bool`: Tests boolean parsing with multiple inputs
  - `test_operator_condition_reevaluates_on_session_state`: Tests condition evaluation logic

**Integration Tests:**
- Scope: Multiple components working together
- Isolation: Uses real test_db fixture
- Examples:
  - `test_repair_returns_expected_structure`: Tests full graph repair pipeline
  - `test_repair_on_clean_installed_agent`: Tests agent installation + repair
  - `test_repair_dead_edge_removal`: Tests database cleanup in context

**E2E/Smoke Tests:**
- Not formally structured as separate category
- Some tests use full app initialization
- Example file: `cockpit_phaseA_smoke.py` (outside test suite)

## Common Patterns

**Async Test Pattern:**
```python
@pytest.mark.asyncio
async def test_async_operation(self, test_db):
    """Async tests use @pytest.mark.asyncio decorator."""
    result = await some_async_function()
    assert result is not None
```

**Error Testing:**
```python
def test_env_returns_default_for_blank(monkeypatch):
    """Test fallback when value is blank/whitespace."""
    monkeypatch.setenv("PRIMARY_KEY", "   ")
    assert env("PRIMARY_KEY", default="fallback") == "fallback"

def test_repair_dry_run_no_changes(self, temp_dir, test_db):
    """Test dry-run mode doesn't modify state."""
    # Setup
    ctx = get_default_context()
    dead_edge = _dead_edge_data(...)
    await ctx.database.save("edge", dead_edge)
    
    # Action
    result = await _repair_to_completion(dry_run=True)
    
    # Assert dry_run flag and no changes
    assert result["dry_run"] is True
    retrieved = await ctx.database.get("edge", dead_edge["id"])
    assert retrieved is not None  # Not deleted in dry-run
```

**State Mutation Testing:**
```python
@pytest.mark.asyncio
async def test_branch_cache_invalidate_clears_entry():
    """BranchCache invalidate(question_name) clears that question's cached target."""
    session = InterviewSession()
    session.context = {}
    branch_cache = BranchCache(session)
    
    branch_cache.set("a", "target_a")
    assert branch_cache.get("a") == "target_a"
    
    branch_cache.invalidate("a")
    assert branch_cache.get("a") is None
```

**Database Transaction Testing:**
```python
@pytest.mark.asyncio
async def test_repair_dead_edge_removal(self, temp_dir, test_db):
    """Repair removes edges whose source or target nodes do not exist."""
    await Root.get()
    
    ctx = get_default_context()
    dead_edge = _dead_edge_data(...)
    await ctx.database.save("edge", dead_edge)
    
    result = await _repair_to_completion(dry_run=False)
    
    assert result["dead_edges_removed"] == 1
    retrieved = await ctx.database.get("edge", dead_edge["id"])
    assert retrieved is None  # Deleted
```

## Test Markers

**Built-in markers:**
- `@pytest.mark.asyncio`: Marks async test functions
  - Enables asyncio_mode = "auto" behavior
  - Function scope loop as per config

**Custom markers:**
- Only "asyncio" defined in pyproject.toml
- No other custom markers in use currently

## Pre-commit Hook

**From `.pre-commit-config.yaml`:**
```yaml
- repo: local
  hooks:
    - id: pytest
      name: pytest
      entry: pytest
      language: system
      pass_filenames: false
      always_run: true
      args: [tests/, -v, --tb=short]
      stages: [manual]
```

**Usage:** Run pre-commit hook manually with `pre-commit run pytest --hook-stage manual`

**Note:** Tests run manually (not on every commit). To check before pushing:
```bash
pytest tests/ -v --tb=short
```

## Test Statistics

**Test count:** 135 test files in `tests/` directory

**Test distribution:**
- Root level tests: ~15 files (env, pruning, schema audit, etc.)
- Core tests: 25+ files in `tests/core/`
- Action tests: scattered in `tests/action/`
- Memory tests: in `tests/memory/`

**File sizes:**
- Most test files: 50-200 lines
- Complex tests: `test_graph_repair.py` (350+ lines with multiple test classes)
- Parametrized tests: 30-50 lines each

## Key Testing Challenges

**Async Coordination:**
- Tests use pytest-asyncio with function-scoped loops
- Database persistence must be immediate (not deferred) for test assertions to work
- Fixture config: `monkeypatch.setenv("JVSPATIAL_ENABLE_DEFERRED_SAVES", "false")`

**Graph Structure Testing:**
- Tests verify complex graph repair operations
- Use _repair_to_completion helper to handle multiple ticks
- Assertions verify counts of different repair types

**Isolation:**
- test_db fixture ensures clean database per test
- monkeypatch ensures environment variables don't leak
- No shared module-level state between tests

---

*Testing analysis: 2026-05-06*
