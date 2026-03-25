"""Async lock manager for memory operations.

Provides per-key async locks to prevent race conditions during concurrent
user/conversation/interaction creation and mutation **within a single Python
process**. Uses TTL-based cleanup to avoid unbounded memory growth.

For conversation chain mutations across multiple workers (e.g. AWS Lambda),
configure :mod:`~jvagent.memory.distributed_conversation_lock` via
``JVAGENT_CONVERSATION_LOCK_REDIS_URL`` or
``JVAGENT_CONVERSATION_LOCK_DYNAMODB_TABLE``; otherwise concurrent invocations
do not share these locks.
"""

import asyncio
import logging
import time
from typing import Dict

logger = logging.getLogger(__name__)

_LOCK_TTL_SECONDS = 30
_CLEANUP_INTERVAL_SECONDS = 120


class MemoryLockManager:
    """Per-key async lock manager with TTL-based cleanup.

    Prevents TOCTOU races in Memory.get_user(), Conversation.add_interaction(),
    and similar create-if-missing flows.
    """

    def __init__(self) -> None:
        self._locks: Dict[str, asyncio.Lock] = {}
        self._timestamps: Dict[str, float] = {}
        self._global_lock = asyncio.Lock()
        self._last_cleanup = time.time()

    async def acquire(self, key: str) -> asyncio.Lock:
        """Get or create a lock for *key* (thread-safe).

        Returns the lock; caller must use ``async with`` on the result.
        """
        async with self._global_lock:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            self._timestamps[key] = time.time()

            now = time.time()
            if now - self._last_cleanup > _CLEANUP_INTERVAL_SECONDS:
                self._last_cleanup = now
                self._cleanup_stale()

            return self._locks[key]

    def _cleanup_stale(self) -> None:
        now = time.time()
        stale = [
            k
            for k, ts in self._timestamps.items()
            if now - ts > _LOCK_TTL_SECONDS
            and k in self._locks
            and not self._locks[k].locked()
        ]
        for k in stale:
            del self._locks[k]
            del self._timestamps[k]
        if stale:
            logger.debug("Cleaned up %d stale memory locks", len(stale))


# Module-level singletons
_user_lock_manager = MemoryLockManager()
_conversation_lock_manager = MemoryLockManager()


def get_user_lock_manager() -> MemoryLockManager:
    return _user_lock_manager


def get_conversation_lock_manager() -> MemoryLockManager:
    return _conversation_lock_manager
