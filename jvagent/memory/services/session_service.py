"""Service facade for memory session operations."""

from typing import Optional, Tuple

from jvagent.memory.conversation import Conversation
from jvagent.memory.manager import Memory
from jvagent.memory.user import User


class SessionService:
    """Thin service wrapper over Memory session APIs."""

    def __init__(self, memory: Memory) -> None:
        self.memory = memory

    async def get_session(
        self,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Tuple[User, Conversation, str, str, bool]:
        return await self.memory.get_session(user_id=user_id, session_id=session_id)

    async def purge(self, user_id: Optional[str] = None) -> int:
        return await self.memory.purge_memory(user_id=user_id)

    async def repair_session(self, recent_minutes: Optional[int] = None):
        return await self.memory.repair_memory(recent_minutes=recent_minutes)
