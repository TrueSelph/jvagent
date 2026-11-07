"""Live lock identity and collection across event-loop lifetimes."""

import asyncio
import gc
import weakref

from jvagent.memory.lock_manager import MemoryLockManager


def test_separate_event_loops_do_not_share_live_locks() -> None:
    manager = MemoryLockManager()
    loop_a = asyncio.new_event_loop()
    loop_b = asyncio.new_event_loop()
    try:
        first = loop_a.run_until_complete(manager.acquire("user:alice"))
        second = loop_b.run_until_complete(manager.acquire("user:alice"))
        assert first is not second
    finally:
        loop_a.close()
        loop_b.close()


def test_same_loop_and_key_share_a_live_lock() -> None:
    manager = MemoryLockManager()

    async def use() -> None:
        first = await manager.acquire("user:bob")
        assert await manager.acquire("user:bob") is first
        assert await manager.acquire("user:other") is not first
        async with first:
            assert first.locked()

    asyncio.run(use())


def test_unused_locks_are_collectable_without_a_sweep() -> None:
    manager = MemoryLockManager()
    loop = asyncio.new_event_loop()
    try:
        lock = loop.run_until_complete(manager.acquire("user:carol"))
        reference = weakref.ref(lock)
        del lock
        gc.collect()
        assert reference() is None
    finally:
        loop.close()
