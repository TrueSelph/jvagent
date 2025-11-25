"""User node for representing users interacting with the agent."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from jvspatial.core import Node
from jvspatial.core.annotations import attribute

if TYPE_CHECKING:
    from jvagent.memory.conversation import Conversation


class User(Node):
    """Internal user model - identifier only, not an account.

    This is a minimal node that only maintains a unique identifier for the person
    interacting with the agent. It does not store email, name, or other account-like
    attributes. The User node is solely for maintaining identity across conversations.

    Entity Relationships:
        - Connected to Memory via incoming edge
        - Connected to Conversations via outgoing edge

    Cascade Delete Behavior:
        - Deleting a User cascades to all connected Conversations
        - Each Conversation deletion cascades to all its Interactions

    Attributes:
        user_id: Unique user identifier (external reference)
        created_at: Timestamp of user creation
        last_seen: Timestamp of last user activity
    """

    user_id: str = attribute(default="", description="Unique user identifier")
    created_at: datetime = attribute(
        default_factory=datetime.utcnow, description="Timestamp of user creation"
    )
    last_seen: datetime = attribute(
        default_factory=datetime.utcnow, description="Timestamp of last user activity"
    )

    async def create_conversation(
        self, session_id: Optional[str] = None, channel: str = "default"
    ) -> "Conversation":
        """Create and connect a new Conversation via edge.

        Args:
            session_id: Optional session identifier. If None, auto-generates one.
            channel: Communication channel (e.g., 'default', 'whatsapp', 'web')

        Returns:
            Newly created Conversation node connected to this User
        """
        from jvagent.memory.conversation import Conversation

        conv = await Conversation.create(
            session_id=session_id or f"sess_{uuid.uuid4().hex[:16]}",
            user_id=self.user_id,
            channel=channel,
        )
        await self.connect(conv)  # Creates edge: User --> Conversation
        return conv

    async def get_conversation_by_session(
        self, session_id: str
    ) -> Optional["Conversation"]:
        """Get connected Conversation by session_id.

        Args:
            session_id: Session identifier to search for

        Returns:
            Conversation node if found, None otherwise
        """
        from jvagent.memory.conversation import Conversation

        conversations: List[Conversation] = await self.nodes(node=Conversation)
        for conv in conversations:
            if conv.session_id == session_id:
                return conv
        return None

    async def list_conversations(self, active_only: bool = False) -> List["Conversation"]:
        """Get all connected Conversations.

        Args:
            active_only: If True, only return active conversations

        Returns:
            List of Conversation nodes
        """
        from jvagent.memory.conversation import Conversation

        conversations: List[Conversation] = await self.nodes(node=Conversation)
        if active_only:
            conversations = [c for c in conversations if c.status == "active"]
        return conversations

    async def get_active_conversation(self) -> Optional["Conversation"]:
        """Get the most recent active conversation.

        Returns:
            Most recent active Conversation, or None if no active conversations
        """
        conversations = await self.list_conversations(active_only=True)
        if not conversations:
            return None
        # Sort by last_interaction_at or created_at, return most recent
        conversations.sort(
            key=lambda c: c.last_interaction_at or c.created_at, reverse=True
        )
        return conversations[0]

    async def record_activity(self) -> None:
        """Update last_seen timestamp to current time."""
        self.last_seen = datetime.utcnow()
        await self.save()
