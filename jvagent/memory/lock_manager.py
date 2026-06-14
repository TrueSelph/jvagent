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
    """Per-(loop, key) async lock manager with TTL-based cleanup.

    Prevents TOCTOU races in :meth:`Memory.get_user`,
    :meth:`Conversation.add_interaction`, and similar create-if-missing
    flows.

    Locks are keyed by ``(id(running_loop), key)`` rather than just
    ``key``.  Without this, a lock instantiated on a destroyed event loop
    (typical on serverless warm starts) survives in the singleton dict and
    later raises ``RuntimeError: ... bound to a different loop`` when a
    fresh request on a new loop tries to acquire it.  AUDIT-memory CRIT-04.

    The TTL sweep additionally drops every entry whose loop is closed, so
    long-running processes that intentionally spin and tear down loops do
    not accumulate dead locks forever.
    """

    def __init__(self) -> None:
        # Keyed by (loop_id, logical_key). Each loop sees its own lock set.
        self._locks: Dict[tuple, asyncio.Lock] = {}
        self._timestamps: Dict[tuple, float] = {}
        # Global lock for dict mutation. Same per-loop pattern.
        self._global_locks_by_loop: Dict[int, asyncio.Lock] = {}
        self._last_cleanup = time.time()

    def _global_lock_for_loop(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        key = id(loop)
        lock = self._global_locks_by_loop.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._global_locks_by_loop[key] = lock
        return lock

    async def acquire(self, key: str) -> asyncio.Lock:
        """Get or create a lock for *key* on the current event loop.

        Returns the lock; caller must use ``async with`` on the result.
        """
        loop = asyncio.get_running_loop()
        composite_key = (id(loop), key)
        async with self._global_lock_for_loop():
            if composite_key not in self._locks:
                self._locks[composite_key] = asyncio.Lock()
            self._timestamps[composite_key] = time.time()

            now = time.time()
            if now - self._last_cleanup > _CLEANUP_INTERVAL_SECONDS:
                self._last_cleanup = now
                self._cleanup_stale()

            return self._locks[composite_key]

    def _cleanup_stale(self) -> None:
        now = time.time()

        # First, drop global-lock entries whose loop is closed. The current
        # loop's entry is preserved because we are running on it.
        try:
            current_loop_id = id(asyncio.get_running_loop())
        except RuntimeError:
            current_loop_id = None
        for loop_id in list(self._global_locks_by_loop.keys()):
            if loop_id == current_loop_id:
                continue
            cached = self._global_locks_by_loop[loop_id]
            inner_loop = getattr(cached, "_loop", None)
            # On Python 3.10+ asyncio.Lock has no ``_loop`` until first await;
            # in that case fall back to treating any non-current loop as
            # dead — it cannot reuse our locks anyway since the per-key keys
            # are also (loop_id, key) tuples bound to that loop.
            try:
                if inner_loop is None or inner_loop.is_closed():
                    self._global_locks_by_loop.pop(loop_id, None)
            except Exception:
                pass

        # Drop every per-key entry whose loop_id is no longer present in the
        # global-lock registry. Those loops are confirmed dead.
        alive_loop_ids = set(self._global_locks_by_loop.keys())
        loop_stale = [
            ck for ck in list(self._locks.keys()) if ck[0] not in alive_loop_ids
        ]

        # Plus the classic TTL-based eviction for live-loop keys that have
        # not been touched in a while.
        ttl_stale = [
            ck
            for ck, ts in self._timestamps.items()
            if now - ts > _LOCK_TTL_SECONDS
            and ck in self._locks
            and not self._locks[ck].locked()
        ]

        stale = set(ttl_stale) | set(loop_stale)
        for ck in stale:
            self._locks.pop(ck, None)
            self._timestamps.pop(ck, None)
        if stale:
            logger.debug(
                "Cleaned up %d stale memory locks (ttl=%d, dead-loop=%d)",
                len(stale),
                len(ttl_stale),
                len(loop_stale),
            )


# Module-level singletons
_user_lock_manager = MemoryLockManager()
_conversation_lock_manager = MemoryLockManager()


def get_user_lock_manager() -> MemoryLockManager:
    return _user_lock_manager


def get_conversation_lock_manager() -> MemoryLockManager:
    return _conversation_lock_manager
