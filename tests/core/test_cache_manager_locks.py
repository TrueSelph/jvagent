"""CacheManager per-loop locks: guarded creation + stale-loop eviction.

Serverless warm starts give each invocation a fresh event loop. Without
eviction, ``CacheManager._loop_locks`` grows one lock set per invocation
forever — the same leak ``App._get_lock`` already guards against.
"""

import asyncio

from jvagent.core.cache import CacheManager


def _grab(cm: CacheManager) -> None:
    async def inner():
        cm._lock("agent")
        cm._lock("action")

    asyncio.run(inner())


def test_lock_evicts_closed_loop_entries():
    """Locks from closed loops are dropped when a new loop takes locks."""
    cm = CacheManager()
    _grab(cm)  # loop 1 (closed after asyncio.run returns)
    assert len(cm._loop_locks) == 2
    _grab(cm)  # loop 2 — stale loop-1 entries must be evicted
    assert len(cm._loop_locks) == 2


def test_lock_same_loop_reuses_instance():
    """Within one loop the same named lock is returned each time."""
    cm = CacheManager()

    async def inner():
        first = cm._lock("agent")
        second = cm._lock("agent")
        assert first is second
        assert cm._lock("action") is not first

    asyncio.run(inner())
