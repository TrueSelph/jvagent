"""Conversation lock manager for WhatsApp action.

This module provides thread-safe conversation access locks to prevent duplicate
conversation creation when multiple messages from the same user arrive simultaneously.

CONFIGURATION RATIONALE:
- CONVERSATION_LOCK_TTL_SECONDS (30): Lock expiry threshold.
  Locks are lightweight and short-lived - 30 seconds is enough for conversation
  lookup/creation while preventing indefinite locks from abandoned operations.

- CONVERSATION_LOCK_CLEANUP_INTERVAL (120): How often to clean stale locks (2 min).
  Less frequent than batch cleanup because locks are smaller and less memory-
  intensive. 2 minutes provides good balance between memory recovery and overhead.

ERROR RECOVERY:
- Locks are only removed if not currently held (prevents removing active locks)
- Lock acquisition is always gated by the global lock to prevent race conditions
- If a lock is held during cleanup, it's skipped until the next cleanup cycle

LAMBDA COMPATIBILITY:
- Cleanup runs inline (awaited) during acquire_lock rather than via background task.
  This ensures cleanup completes within the same request and doesn't depend on
  background tasks that may be frozen when Lambda returns.
"""

import asyncio
import logging
import time
from typing import Dict

logger = logging.getLogger(__name__)

CONVERSATION_LOCK_TTL_SECONDS = 30  # Lock expires after 30 seconds
CONVERSATION_LOCK_CLEANUP_INTERVAL = 120  # Run cleanup every 2 minutes


class ConversationLockManager:
    """Thread-safe manager for conversation access locks.
    
    Prevents race conditions when multiple messages from the same user
    trigger concurrent conversation lookups that could create duplicates.
    """
    
    def __init__(self):
        self._locks: Dict[str, asyncio.Lock] = {}
        self._lock_timestamps: Dict[str, float] = {}
        self._global_lock = asyncio.Lock()
        self._last_cleanup = time.time()
    
    async def acquire_lock(self, user_id: str) -> asyncio.Lock:
        """Get or create a lock for a specific user (thread-safe).
        
        Lambda-compatible: Runs cleanup inline when needed (no background tasks).
        
        Returns the lock - caller must use it with `async with` pattern.
        """
        async with self._global_lock:
            if user_id not in self._locks:
                self._locks[user_id] = asyncio.Lock()
            self._lock_timestamps[user_id] = time.time()
            
            # Run cleanup inline if needed (Lambda-compatible: no background task)
            current_time = time.time()
            if current_time - self._last_cleanup > CONVERSATION_LOCK_CLEANUP_INTERVAL:
                self._last_cleanup = current_time
                # Cleanup inline rather than in background task
                await self._cleanup_stale_locks_inline()
            
            return self._locks[user_id]
    
    async def _cleanup_stale_locks_inline(self) -> None:
        """Remove locks that haven't been used recently (inline, no global lock needed).
        
        Lambda-compatible: Runs in the same request rather than background task.
        Called from within acquire_lock which already holds _global_lock.
        """
        current_time = time.time()
        stale_users = []
        
        # Already have _global_lock from acquire_lock, so don't re-acquire
        for user_id, timestamp in list(self._lock_timestamps.items()):
            if current_time - timestamp > CONVERSATION_LOCK_TTL_SECONDS:
                # Only remove if lock is not currently held
                if user_id in self._locks and not self._locks[user_id].locked():
                    stale_users.append(user_id)
        
        for user_id in stale_users:
            del self._locks[user_id]
            del self._lock_timestamps[user_id]
        
        if stale_users:
            logger.debug(f"Cleaned up {len(stale_users)} stale conversation locks")
