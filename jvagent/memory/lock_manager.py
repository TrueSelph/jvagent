"""Per-event-loop locks shared by live callers, without expiry races.

Owners and queued waiters retain their lock through ordinary Python references.
Weak caches release unused locks and event loops without a timer or access to
asyncio internals. Cross-worker coordination remains the distributed host's job.
"""

import asyncio
from weakref import WeakKeyDictionary, WeakValueDictionary


class MemoryLockManager:
    """Share one live lock per event loop and logical resource key."""

    def __init__(self) -> None:
        self._locks: WeakKeyDictionary[
            asyncio.AbstractEventLoop, WeakValueDictionary[str, asyncio.Lock]
        ] = WeakKeyDictionary()

    async def acquire(self, key: str) -> asyncio.Lock:
        """Return a lock; the caller must acquire it with ``async with``.

        Lookup contains no await, so one loop cannot interleave creation of the
        same entry. Live owners/waiters keep that entry reachable through a
        handoff, even while the lock is temporarily unlocked.
        """
        loop = asyncio.get_running_loop()
        locks = self._locks.get(loop)
        if locks is None:
            locks = WeakValueDictionary()
            self._locks[loop] = locks
        lock = locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            locks[key] = lock
        return lock


_user_lock_manager = MemoryLockManager()
_conversation_lock_manager = MemoryLockManager()


def get_user_lock_manager() -> MemoryLockManager:
    return _user_lock_manager


def get_conversation_lock_manager() -> MemoryLockManager:
    return _conversation_lock_manager
