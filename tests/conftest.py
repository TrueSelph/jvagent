"""Shared pytest fixtures for jvagent tests."""

import os
import tempfile
from pathlib import Path

import pytest
from jvspatial.core.context import GraphContext, set_default_context
from jvspatial.db.jsondb import JsonDB


@pytest.fixture(autouse=True)
def _clear_jvspatial_load_env_cache():
    """Invalidate jvspatial :func:`load_env` so per-test env changes apply.

    jvagent uses :func:`jvspatial.env.get_environment_mode` and deferred-save
    settings from cached env; tests patch ``os.environ`` or use ``monkeypatch``.
    """
    from jvspatial.env import clear_load_env_cache

    clear_load_env_cache()
    yield


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture(scope="function")
async def test_db(temp_dir, monkeypatch):
    """Initialize test database and GraphContext."""
    # Immediate persistence: deferred Interaction/Conversation saves break tests
    # that reload entities with Interaction.get() or aggregate from the DB.
    monkeypatch.setenv("JVSPATIAL_ENABLE_DEFERRED_SAVES", "false")
    from jvspatial.env import clear_load_env_cache

    clear_load_env_cache()

    test_db_path = temp_dir / "test_jvdb"
    test_db_path.mkdir()

    # Create JsonDB instance
    db = JsonDB(base_path=str(test_db_path))

    # Create GraphContext with the test database
    ctx = GraphContext(database=db)

    # Set as default context so all entities use this database
    set_default_context(ctx)

    yield test_db_path

    # Cleanup is handled by tempfile.TemporaryDirectory
