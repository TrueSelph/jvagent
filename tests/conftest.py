"""Shared pytest fixtures for jvagent tests."""

import tempfile
from pathlib import Path

import pytest
from jvspatial.core.context import (
    GraphContext,
    _default_context_var,
    clear_default_context,
    set_default_context,
)
from jvspatial.db.jsondb import JsonDB


@pytest.fixture(autouse=True)
def _clear_jvspatial_load_env_cache():
    """Shared test setup hook (kept for fixture compatibility)."""
    yield


@pytest.fixture(autouse=True)
def _ensure_default_graph_context(tmp_path_factory, monkeypatch):
    """Bind a per-test default GraphContext for any test that touches jvspatial.

    Historically jvagent relied on ``set_default_context`` mutating a process
    global, so the first test in a run that needed a context implicitly
    "primed" every subsequent test. With the per-task ContextVar now in
    place that leakage is gone — tests that previously inherited a default
    must now have one set up for them. This autouse fixture creates a
    throwaway JsonDB rooted under a fresh temp directory and binds it via
    ``set_default_context`` so each test starts from a known good state.

    Tests that need richer setup (deferred-save tuning, asserting on the
    DB path, etc.) request the explicit ``test_db`` fixture instead.
    """
    monkeypatch.setenv("JVSPATIAL_ENABLE_DEFERRED_SAVES", "false")
    db_root = tmp_path_factory.mktemp("autouse_jvdb")
    db = JsonDB(base_path=str(db_root))
    ctx = GraphContext(database=db)
    token = set_default_context(ctx)
    try:
        yield ctx
    finally:
        # ``reset`` is the precise restore; if the test mutated the
        # contextvar in a way that invalidates the token (rare), fall back
        # to an explicit clear so we never leave per-task state behind.
        try:
            _default_context_var.reset(token)
        except (ValueError, LookupError):
            clear_default_context()


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture(scope="function")
async def test_db(temp_dir, monkeypatch):
    """Initialize a richer test database + GraphContext.

    Overrides the autouse default with a context tied to the per-test
    ``temp_dir`` so tests that rely on a stable path (e.g. for asserting
    persisted files) can locate it. Cleanup of the per-task contextvar
    runs via the autouse fixture's restore.
    """
    monkeypatch.setenv("JVSPATIAL_ENABLE_DEFERRED_SAVES", "false")

    test_db_path = temp_dir / "test_jvdb"
    test_db_path.mkdir()

    db = JsonDB(base_path=str(test_db_path))
    ctx = GraphContext(database=db)
    set_default_context(ctx)

    yield test_db_path

    # Cleanup is handled by tempfile.TemporaryDirectory and the autouse fixture.
