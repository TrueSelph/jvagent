"""Thread-safe conversation access locks for channel webhook handlers."""

import asyncio
import logging
import time
from typing import Dict

logger = logging.getLogger(__name__)

CONVERSATION_LOCK_TTL_SECONDS = 30
CONVERSATION_LOCK_CLEANUP_INTERVAL = 120


class ConversationLockManager:
    """Prevents race conditions when concurrent webhooks look up conversations."""

    def __init__(self) -> None:
        self._locks: Dict[str, asyncio.Lock] = {}
        self._lock_timestamps: Dict[str, float] = {}
        self._global_lock = asyncio.Lock()
        self._last_cleanup = time.time()

    async def acquire_lock(self, user_id: str) -> asyncio.Lock:
        """Get or create a lock for a specific user (thread-safe)."""
        async with self._global_lock:
            if user_id not in self._locks:
                self._locks[user_id] = asyncio.Lock()
            self._lock_timestamps[user_id] = time.time()

            current_time = time.time()
            if current_time - self._last_cleanup > CONVERSATION_LOCK_CLEANUP_INTERVAL:
                self._last_cleanup = current_time
                await self._cleanup_stale_locks_inline()

            return self._locks[user_id]

    async def _cleanup_stale_locks_inline(self) -> None:
        current_time = time.time()
        stale_users = []

        for user_id, timestamp in list(self._lock_timestamps.items()):
            if current_time - timestamp > CONVERSATION_LOCK_TTL_SECONDS:
                if user_id in self._locks and not self._locks[user_id].locked():
                    stale_users.append(user_id)

        for user_id in stale_users:
            del self._locks[user_id]
            del self._lock_timestamps[user_id]

        if stale_users:
            logger.debug("Cleaned up %s stale conversation locks", len(stale_users))
