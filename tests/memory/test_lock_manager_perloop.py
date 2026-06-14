"""Per-event-loop lock manager (AUDIT-memory CRIT-04).

Locks must be keyed by (loop_id, key), not just key. Otherwise, a lock
created on a destroyed loop is returned to a fresh loop and raises
``RuntimeError: ... bound to a different loop`` at acquire time.
"""

import asyncio

from jvagent.memory.lock_manager import MemoryLockManager


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_two_separate_event_loops_do_not_share_per_key_locks():
    mgr = MemoryLockManager()

    async def _acquire_and_use(key: str) -> bool:
        lock = await mgr.acquire(key)
        async with lock:
            return True

    # First loop populates the manager.
    loop_a = asyncio.new_event_loop()
    try:
        result_a = loop_a.run_until_complete(_acquire_and_use("user:alice"))
    finally:
        loop_a.close()
    assert result_a is True

    # Second loop must NOT inherit loop_a's lock.
    loop_b = asyncio.new_event_loop()
    try:
        result_b = loop_b.run_until_complete(_acquire_and_use("user:alice"))
    finally:
        loop_b.close()
    assert result_b is True


def test_composite_key_includes_loop_id():
    mgr = MemoryLockManager()

    async def _seed():
        await mgr.acquire("user:bob")

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_seed())
    finally:
        loop.close()

    keys = list(mgr._locks.keys())
    assert keys, "expected at least one entry"
    for k in keys:
        assert isinstance(k, tuple) and len(k) == 2
        loop_id, logical = k
        assert isinstance(loop_id, int)
        assert logical == "user:bob"


def test_cleanup_drops_dead_loop_entries():
    mgr = MemoryLockManager()

    async def _seed():
        await mgr.acquire("user:carol")

    loop_a = asyncio.new_event_loop()
    try:
        loop_a.run_until_complete(_seed())
    finally:
        loop_a.close()
    assert any(k[1] == "user:carol" for k in mgr._locks)

    # Force the manager's TTL sweep on a fresh loop. Dead-loop entries
    # should be evicted alongside the seed entry.
    async def _trigger_sweep():
        mgr._last_cleanup = 0.0  # force the cleanup branch
        await mgr.acquire("user:dave")

    loop_b = asyncio.new_event_loop()
    try:
        loop_b.run_until_complete(_trigger_sweep())
    finally:
        loop_b.close()

    # The dead-loop entry for "user:carol" must be gone.
    assert not any(k[1] == "user:carol" for k in mgr._locks), mgr._locks
