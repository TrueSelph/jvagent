"""A live waiter retains lock identity across any cache maintenance."""

import asyncio
import time
from typing import Any

import pytest

from jvagent.memory.lock_manager import MemoryLockManager


@pytest.mark.asyncio
async def test_release_handoff_cannot_be_split_by_expiry(monkeypatch: Any) -> None:
    manager = MemoryLockManager()
    first = await manager.acquire("shared")
    await first.acquire()
    queued = asyncio.Event()

    async def waiter() -> None:
        lock = await manager.acquire("shared")
        queued.set()
        async with lock:
            pass

    task = asyncio.create_task(waiter())
    await queued.wait()
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now + 180)
    first.release()
    try:
        # No scheduling yield: the notified waiter still owns a live reference.
        await manager.acquire("trigger-maintenance")
        next_lock = await manager.acquire("shared")
        assert next_lock is first
    finally:
        await task
