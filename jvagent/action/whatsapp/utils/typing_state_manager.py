"""Typing state manager for WhatsApp action.

Tracks per-user typing state for concurrent-safe typing indicator management.
"""

import asyncio
from typing import Dict


class TypingStateManager:
    """Thread-safe manager for per-user typing state.

    Tracks which users currently have typing indicators active.
    Used to prevent duplicate typing API calls and manage state across
    concurrent interactions.
    """

    def __init__(self) -> None:
        self._typing: Dict[str, bool] = {}
        self._lock = asyncio.Lock()

    async def set_typing(self, user_id: str, value: bool) -> bool:
        """Set typing state for a user.

        Args:
            user_id: User identifier
            value: True if typing, False if not

        Returns:
            True if state changed, False if already in requested state
        """
        async with self._lock:
            prev = self._typing.get(user_id, False)
            if prev == value:
                return False
            self._typing[user_id] = value
            return True

    async def is_typing(self, user_id: str) -> bool:
        """Check if user is currently typing.

        Args:
            user_id: User identifier

        Returns:
            True if user has typing indicator active
        """
        async with self._lock:
            return self._typing.get(user_id, False)
