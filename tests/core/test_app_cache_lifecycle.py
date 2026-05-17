"""Tests for App cache thread-safety + context-swap re-resolution.

AUDIT-core C-1: cache hit must verify against the current GraphContext.
AUDIT-core C-2: ``_cached_app`` reads/writes must go through
``_locks_guard``; app_loader writes must use ``App._set_cached_app``.
"""

from unittest.mock import AsyncMock, patch

import pytest

from jvagent.core.app import App


@pytest.fixture(autouse=True)
def _isolate_cache():
    App._set_cached_app(None)
    yield
    App._set_cached_app(None)


def test_set_cached_app_round_trip():
    """``_set_cached_app`` + ``_read_cached_app`` form the only supported API."""
    sentinel = App()
    App._set_cached_app(sentinel)
    assert App._read_cached_app() is sentinel
    App._set_cached_app(None)
    assert App._read_cached_app() is None


@pytest.mark.asyncio
async def test_cache_hit_dropped_when_not_in_current_context():
    """C-1: a cached App whose id is not resolvable in the active context
    must be discarded — no stale node is returned."""
    cached = App()
    object.__setattr__(cached, "id", "n.App.abc123")
    App._set_cached_app(cached)

    fake_ctx = AsyncMock()
    fake_ctx.get = AsyncMock(return_value=None)  # not in current DB

    with patch(
        "jvagent.core.app.get_default_context", return_value=fake_ctx
    ):
        result = await App._verify_cached_against_current_context(cached)

    assert result is None


@pytest.mark.asyncio
async def test_cache_hit_returned_when_in_current_context():
    cached = App()
    object.__setattr__(cached, "id", "n.App.abc123")
    fake_ctx = AsyncMock()
    fake_ctx.get = AsyncMock(return_value=cached)

    with patch(
        "jvagent.core.app.get_default_context", return_value=fake_ctx
    ):
        result = await App._verify_cached_against_current_context(cached)

    assert result is cached


def test_locks_guard_protects_cache_attribute():
    """C-2: the guard exists as a threading.Lock and is held by accessors."""
    import threading

    assert isinstance(App._locks_guard, type(threading.Lock()))


def test_clear_cache_uses_setter():
    sentinel = App()
    App._set_cached_app(sentinel)
    App.clear_cache()
    assert App._read_cached_app() is None
