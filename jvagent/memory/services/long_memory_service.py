"""Service helpers for user long-memory graph orchestration."""

from typing import Any, Dict

from jvagent.memory.long_memory_retrieval_utils import resolve_long_memory_collection
from jvagent.memory.user import User
from jvagent.memory.user_long_memory import UserLongMemory


class LongMemoryService:
    """Encapsulate common long-memory read/write helpers."""

    async def get_memory_content(self, user: User) -> Dict[str, Dict[str, Any]]:
        long_memory = await UserLongMemory.get_for_user(user)
        if not long_memory:
            return {}
        categories = await long_memory.get_all_categories()
        payload: Dict[str, Dict[str, Any]] = {}
        for cat in categories:
            if cat.is_empty():
                continue
            payload[cat.category] = {
                "title": cat.title,
                "content": cat.content,
                "updated_at": cat.updated_at.isoformat() if cat.updated_at else None,
            }
        return payload

    def resolve_collection(self, *, agent_id: str, suffix: str) -> str:
        """Build the PageIndex collection name for the given agent + suffix.

        Bridges to :func:`resolve_long_memory_collection`, which takes
        ``collection_attr`` and ``config`` rather than a flat ``suffix``.
        The previous implementation passed an unrecognized ``suffix=`` kwarg
        and would raise ``TypeError`` on the first call.  AUDIT-memory CRIT-02.
        """
        return resolve_long_memory_collection(
            agent_id=agent_id,
            collection_attr=suffix,
            config=None,
        )
