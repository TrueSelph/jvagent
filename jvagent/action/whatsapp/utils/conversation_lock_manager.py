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
"""

import asyncio
import logging
import time
from typing import Dict

from .task_helpers import create_background_task

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
        
        Returns the lock - caller must use it with `async with` pattern.
        """
        async with self._global_lock:
            if user_id not in self._locks:
                self._locks[user_id] = asyncio.Lock()
            self._lock_timestamps[user_id] = time.time()
            
            # Schedule cleanup if needed
            current_time = time.time()
            if current_time - self._last_cleanup > CONVERSATION_LOCK_CLEANUP_INTERVAL:
                self._last_cleanup = current_time
                create_background_task(self._cleanup_stale_locks(), name="conversation_lock_cleanup")
            
            return self._locks[user_id]
    
    async def _cleanup_stale_locks(self) -> None:
        """Remove locks that haven't been used recently."""
        current_time = time.time()
        stale_users = []
        
        async with self._global_lock:
            for user_id, timestamp in list(self._lock_timestamps.items()):
                if current_time - timestamp > CONVERSATION_LOCK_TTL_SECONDS:
                    # Only remove if lock is not currently held
                    if user_id in self._locks and not self._locks[user_id].locked():
                        stale_users.append(user_id)
            
            for user_id in stale_users:
                del self._locks[user_id]
                del self._lock_timestamps[user_id]
