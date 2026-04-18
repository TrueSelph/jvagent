"""Repository wrapper for interaction chain access."""

from typing import List, Optional

from jvagent.memory.conversation import Conversation
from jvagent.memory.interaction import Interaction


class InteractionRepository:
    """Small repository facade over Conversation/Interaction graph operations."""

    async def append(
        self,
        conversation: Conversation,
        utterance: str,
        channel: str = "default",
    ) -> Interaction:
        return await conversation.add_interaction(utterance=utterance, channel=channel)

    async def list(
        self,
        conversation: Conversation,
        limit: int = 0,
        reverse: bool = False,
    ) -> List[Interaction]:
        return await conversation.get_interactions(limit=limit, reverse=reverse)

    async def prune(
        self, conversation: Conversation, limit: Optional[int] = None
    ) -> int:
        return await conversation.prune_excess_interactions(interaction_limit=limit)
