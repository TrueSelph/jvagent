"""Tests for UserLongMemory graph helpers (no database)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.memory.user_long_memory import DEFAULT_CATEGORIES, UserLongMemory


class _LMStub:
    """Plain object: jvspatial Node forbids assigning arbitrary methods on instances."""

    pass


@pytest.mark.asyncio
async def test_ensure_default_categories_short_circuit_when_all_present() -> None:
    stub = _LMStub()
    nodes = []
    for cat in DEFAULT_CATEGORIES:
        m = MagicMock()
        m.category = cat
        nodes.append(m)
    stub.get_all_categories = AsyncMock(return_value=nodes)
    stub.get_or_create_category = AsyncMock()

    out = await UserLongMemory.ensure_default_categories(stub)

    stub.get_or_create_category.assert_not_called()
    assert [n.category for n in out] == list(DEFAULT_CATEGORIES)


@pytest.mark.asyncio
async def test_ensure_default_categories_creates_missing() -> None:
    stub = _LMStub()
    created = []

    async def _goc(cat, title=None):
        m = MagicMock()
        m.category = cat
        created.append(cat)
        return m

    stub.get_all_categories = AsyncMock(return_value=[])
    stub.get_or_create_category = AsyncMock(side_effect=_goc)

    out = await UserLongMemory.ensure_default_categories(stub)

    assert created == list(DEFAULT_CATEGORIES)
    assert len(out) == len(DEFAULT_CATEGORIES)
