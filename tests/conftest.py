"""Shared pytest fixtures for jvagent tests."""

import os
import tempfile
from pathlib import Path

import pytest
from jvspatial.core.context import GraphContext, set_default_context
from jvspatial.db.jsondb import JsonDB


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture(scope="function")
async def test_db(temp_dir):
    """Initialize test database and GraphContext."""
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
