"""Conversation lock manager for memory add_interaction.

This module provides thread-safe per-conversation locks to prevent duplicate
bidirectional edges when multiple requests for the same session arrive
simultaneously. Without locking, concurrent add_interaction calls can both
chain to the same last_interaction, creating multiple edges from one node.

CONFIGURATION RATIONALE:
- CONVERSATION_LOCK_TTL_SECONDS (30): Lock expiry threshold.
- CONVERSATION_LOCK_CLEANUP_INTERVAL (120): How often to clean stale locks (2 min).

LAMBDA COMPATIBILITY:
- Cleanup runs inline (awaited) during acquire_lock rather than via background task.
"""

import asyncio
import logging
import time
from typing import Dict

logger = logging.getLogger(__name__)

CONVERSATION_LOCK_TTL_SECONDS = 30
CONVERSATION_LOCK_CLEANUP_INTERVAL = 120


class ConversationLockManager:
    """Thread-safe manager for per-conversation add_interaction locks.

    Prevents race conditions when concurrent requests for the same session
    both call add_interaction, which could create multiple bidirectional
    edges from one interaction node (invalid chain).
    """

    def __init__(self) -> None:
        self._locks: Dict[str, asyncio.Lock] = {}
        self._lock_timestamps: Dict[str, float] = {}
        self._global_lock = asyncio.Lock()
        self._last_cleanup = time.time()

    async def acquire_lock(self, session_id: str) -> asyncio.Lock:
        """Get or create a lock for a specific session (thread-safe).

        Returns the lock - caller must use it with `async with` pattern.
        """
        # Use conversation id as fallback if session_id empty (e.g. during creation)
        key = session_id or "default"
        async with self._global_lock:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            self._lock_timestamps[key] = time.time()

            current_time = time.time()
            if current_time - self._last_cleanup > CONVERSATION_LOCK_CLEANUP_INTERVAL:
                self._last_cleanup = current_time
                await self._cleanup_stale_locks_inline()

            return self._locks[key]

    async def _cleanup_stale_locks_inline(self) -> None:
        """Remove locks that haven't been used recently."""
        current_time = time.time()
        stale_keys = []

        for key, timestamp in list(self._lock_timestamps.items()):
            if current_time - timestamp > CONVERSATION_LOCK_TTL_SECONDS:
                if key in self._locks and not self._locks[key].locked():
                    stale_keys.append(key)

        for key in stale_keys:
            del self._locks[key]
            del self._lock_timestamps[key]

        if stale_keys:
            logger.debug(f"Cleaned up {len(stale_keys)} stale conversation locks")


# Module-level singleton for add_interaction locking
_conversation_lock_manager: ConversationLockManager | None = None


def get_conversation_lock_manager() -> ConversationLockManager:
    """Get the module-level conversation lock manager."""
    global _conversation_lock_manager
    if _conversation_lock_manager is None:
        _conversation_lock_manager = ConversationLockManager()
    return _conversation_lock_manager
