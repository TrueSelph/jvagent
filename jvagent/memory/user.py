"""User node for representing users interacting with the agent."""

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

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
        usage: Cumulative token spend and model call metrics across all interactions
        created_at: Timestamp of user creation
        last_seen: Timestamp of last user activity
    """

    user_id: str = attribute(
        indexed=True,
        index_unique=True,
        default="",
        description="Unique user identifier",
    )
    name: Optional[str] = attribute(
        default=None, description="User's preferred name (raw input)"
    )
    display_name: Optional[str] = attribute(
        default=None, description="Formatted display name for addressing the user"
    )
    user_model: Dict[str, Any] = attribute(
        default_factory=dict,
        description="Compressed collection of facts and preferences about the user",
    )
    usage: Dict[str, Any] = attribute(
        default_factory=dict,
        description="Cumulative token spend and model call metrics across all interactions",
    )
    created_at: datetime = attribute(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp of user creation",
    )
    last_seen: datetime = attribute(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp of last user activity",
    )

    async def create_conversation(
        self,
        session_id: Optional[str] = None,
        channel: str = "default",
        interaction_limit: Optional[int] = None,
    ) -> "Conversation":
        """Create and connect a new Conversation via edge.

        When interaction_limit is not provided, it is taken from the agent's default.

        Args:
            session_id: Optional session identifier. If None, auto-generates one.
            channel: Communication channel (e.g., 'default', 'whatsapp', 'web')
            interaction_limit: Optional interaction limit. If None, uses agent's default.

        Returns:
            Newly created Conversation node connected to this User
        """
        from jvagent.core.app import App
        from jvagent.memory.conversation import Conversation
        from jvagent.memory.manager import Memory

        # Get agent's default interaction_limit if not provided
        if interaction_limit is None:
            agent = await self.get_agent()
            if agent and hasattr(agent, "interaction_limit"):
                interaction_limit = agent.interaction_limit

        app = await App.get()
        now = await app.now() if app else datetime.now(timezone.utc)

        conv = await Conversation.create(
            session_id=session_id or f"sess_{uuid.uuid4().hex[:16]}",
            user_id=self.user_id,
            channel=channel,
            interaction_limit=interaction_limit or 0,
            created_at=now,
        )
        await self.connect(conv)  # Creates edge: User --> Conversation

        # Update Memory's total_conversations counter
        memory = await self.node(direction="in", node=Memory)
        if memory:
            memory.total_conversations += 1
            await memory.save()

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
        return await Conversation.find_one(
            {
                "context.session_id": session_id,
                "context.user_id": self.user_id,
            }
        )

    async def list_conversations(
        self, active_only: bool = False
    ) -> List["Conversation"]:
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
        from jvagent.core.app import App

        app = await App.get()
        self.last_seen = await app.now() if app else datetime.now(timezone.utc)
        await self.save()

    def get_name(self) -> Optional[str]:
        """Return the raw name provided by the user."""
        return self.name

    async def set_name(self, name: str, display_name: Optional[str] = None) -> None:
        """Set the user's name and optionally a formatted display name."""
        self.name = name
        # If display_name is explicitly passed, use it; otherwise default to name
        self.display_name = display_name if display_name is not None else name
        await self.save()

    async def set_display_name(self, display_name: str) -> None:
        """Set the display name independently of the raw name."""
        self.display_name = display_name
        await self.save()

    def get_display_name(self) -> str:
        """Get a formatted name for addressing the user."""
        if self.display_name:
            return self.display_name
        if self.name:
            return self.name
        return "user"

    async def update_user_model(
        self,
        facts: Optional[List[str]] = None,
        preferences: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Update the stored user model facts/preferences and timestamp."""
        if not self.user_model:
            self.user_model = {"facts": [], "preferences": {}, "last_updated": None}

        if facts:
            self.user_model["facts"].extend(facts)
        if preferences:
            self.user_model["preferences"].update(preferences)

        from jvagent.core.app import App

        app = await App.get()
        self.user_model["last_updated"] = (
            await app.now() if app else datetime.now(timezone.utc)
        )
        await self.save()

    def get_user_model(self) -> Dict[str, Any]:
        """Return the current user model with sensible defaults."""
        if not self.user_model:
            return {"facts": [], "preferences": {}, "last_updated": None}
        return self.user_model

    async def add_usage_from_interaction(self, usage: Dict[str, Any]) -> None:
        """Increment cumulative usage stats from an interaction's usage.

        Args:
            usage: Usage dict from Interaction.compute_usage()
        """
        if not usage:
            return

        from jvagent.core.app import App

        if not self.usage:
            self.usage = {
                "total_tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "model_call_count": 0,
                "estimated_cost_usd": 0.0,
                "total_duration_seconds": 0.0,
                "interaction_count": 0,
                "last_updated": None,
            }

        self.usage["total_tokens"] = (
            self.usage.get("total_tokens", 0)
            + usage.get("total_tokens", 0)
        )
        self.usage["prompt_tokens"] = (
            self.usage.get("prompt_tokens", 0)
            + usage.get("prompt_tokens", 0)
        )
        self.usage["completion_tokens"] = (
            self.usage.get("completion_tokens", 0)
            + usage.get("completion_tokens", 0)
        )
        self.usage["model_call_count"] = (
            self.usage.get("model_call_count", 0)
            + usage.get("model_call_count", 0)
        )
        self.usage["estimated_cost_usd"] = round(
            self.usage.get("estimated_cost_usd", 0.0)
            + usage.get("estimated_cost_usd", 0.0),
            6,
        )
        self.usage["total_duration_seconds"] = round(
            self.usage.get("total_duration_seconds", 0.0)
            + usage.get("total_duration_seconds", 0.0),
            3,
        )
        self.usage["interaction_count"] = (
            self.usage.get("interaction_count", 0) + 1
        )

        app = await App.get()
        self.usage["last_updated"] = (
            await app.now() if app else datetime.now(timezone.utc)
        ).isoformat()
        await self.save()

    def get_usage_statistics(self) -> Dict[str, Any]:
        """Return usage stats with sensible defaults."""
        if not self.usage:
            return {
                "total_tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "model_call_count": 0,
                "estimated_cost_usd": 0.0,
                "total_duration_seconds": 0.0,
                "interaction_count": 0,
                "last_updated": None,
            }
        return self.usage
