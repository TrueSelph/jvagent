"""User node for representing users interacting with the agent."""

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional, Any

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

    user_id: str = attribute(indexed=True, index_unique=True, default="", description="Unique user identifier")
    created_at: datetime = attribute(
        default_factory=lambda: datetime.now(timezone.utc), description="Timestamp of user creation"
    )
    last_seen: datetime = attribute(
        default_factory=lambda: datetime.now(timezone.utc), description="Timestamp of last user activity"
    )

    async def create_conversation(
        self,
        session_id: Optional[str] = None,
        channel: str = "default",
        interaction_limit: Optional[int] = None,
    ) -> "Conversation":
        """Create and connect a new Conversation via edge.

        Args:
            session_id: Optional session identifier. If None, auto-generates one.
            channel: Communication channel (e.g., 'default', 'whatsapp', 'web')
            interaction_limit: Optional interaction limit. If None, uses agent's default.

        Returns:
            Newly created Conversation node connected to this User
        """
        from jvagent.memory.conversation import Conversation
        from jvagent.memory.manager import Memory

        # Get agent's default interaction_limit if not provided
        if interaction_limit is None:
            agent = await self.get_agent()
            if agent and hasattr(agent, "interaction_limit"):
                interaction_limit = agent.interaction_limit

        conv = await Conversation.create(
            session_id=session_id or f"sess_{uuid.uuid4().hex[:16]}",
            user_id=self.user_id,
            channel=channel,
            interaction_limit=interaction_limit or 0,
        )
        await self.connect(conv)  # Creates edge: User --> Conversation
        return conv

    async def get_agent(self) -> Optional[Any]:
        """Get the Agent node this User belongs to.

        Traverses: User -> Memory (incoming edge) -> Agent.

        Returns:
            Agent instance if found, None otherwise
        """
        from jvagent.memory.manager import Memory

        # Get Memory node (User is connected to Memory via incoming edge)
        memory = await self.node(direction="in", node=Memory)
        if memory:
            # Get Agent from Memory using its get_agent() method
            return await memory.get_agent()
        return None

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

        # Use find_one for optimal performance
        # Filter by both session_id and user_id to ensure it belongs to this user
        return await Conversation.find_one({
            "context.session_id": session_id,
            "context.user_id": self.user_id,
        })

    async def list_conversations(self, active_only: bool = False) -> List["Conversation"]:
        """Get all connected Conversations.

        Args:
            active_only: If True, only return active conversations

        Returns:
            List of Conversation nodes
        """
        from jvagent.memory.conversation import Conversation

        # Use direct database search for optimal performance
        query = {"context.user_id": self.user_id}
        if active_only:
            query["context.status"] = "active"
        
        return await Conversation.find(query)

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
        self.last_seen = datetime.now(timezone.utc)
        await self.save()
