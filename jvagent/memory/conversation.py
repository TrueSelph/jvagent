"""Conversation node for managing conversation sessions."""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.core import Node
from jvspatial.core.annotations import attribute

if TYPE_CHECKING:
    from jvagent.memory.interaction import Interaction


class Conversation(Node):
    """Session-based conversation. session_id can be set or auto-generated.

    The Conversation node represents a conversation session belonging to a User.
    Each conversation has a session_id that is the primary mechanism for continuing
    active conversations.

    Entity Relationships:
        - Connected to User via incoming edge
        - Connected to Interactions via outgoing edge

    Cascade Delete Behavior:
        - Deleting a Conversation cascades to all connected Interactions

    Attributes:
        session_id: Session identifier (can be set or auto-generated)
        user_id: Owning user's identifier
        status: Conversation status (active, archived, closed)
        channel: Communication channel
        created_at: Timestamp of conversation creation
        last_interaction_at: Timestamp of last interaction
        interaction_count: Number of interactions in this conversation
        context: Conversation context dictionary for storing state
    """

    session_id: str = attribute(default="", description="Session identifier")
    user_id: str = attribute(default="", description="Owning user ID")
    status: str = attribute(
        default="active", description="Conversation status: active, archived, closed"
    )
    channel: str = attribute(default="default", description="Communication channel")
    created_at: datetime = attribute(
        default_factory=datetime.utcnow, description="Timestamp of creation"
    )
    last_interaction_at: Optional[datetime] = attribute(
        default=None, description="Timestamp of last interaction"
    )
    interaction_count: int = attribute(
        default=0, description="Number of interactions"
    )
    context: Dict[str, Any] = attribute(
        default_factory=dict, description="Conversation context dictionary"
    )

    async def add_interaction(self, interaction: "Interaction") -> "Interaction":
        """Connect an Interaction to this Conversation via edge.

        Args:
            interaction: Interaction node to connect

        Returns:
            The connected Interaction node
        """
        await self.connect(interaction)  # Creates edge: Conversation --> Interaction
        self.interaction_count += 1
        self.last_interaction_at = datetime.utcnow()
        await self.save()
        return interaction

    async def create_interaction(
        self,
        utterance: str,
        channel: Optional[str] = None,
    ) -> "Interaction":
        """Create a new Interaction and connect it to this Conversation.

        Args:
            utterance: User's input text
            channel: Optional channel override (defaults to conversation's channel)

        Returns:
            Newly created and connected Interaction node
        """
        from jvagent.memory.interaction import Interaction

        interaction = await Interaction.create(
            conversation_id=self.id,
            user_id=self.user_id,
            utterance=utterance,
            channel=channel or self.channel,
        )
        return await self.add_interaction(interaction)

    async def get_interactions(self, limit: int = 50) -> List["Interaction"]:
        """Get connected Interactions ordered by timestamp.

        Args:
            limit: Maximum number of interactions to return (0 for all)

        Returns:
            List of Interaction nodes sorted by started_at
        """
        from jvagent.memory.interaction import Interaction

        interactions: List[Interaction] = await self.nodes(node=Interaction)
        # Sort by started_at
        interactions.sort(key=lambda i: i.started_at)
        if limit:
            return interactions[-limit:]
        return interactions

    async def get_transcript(
        self,
        limit: int = 10,
        max_statement_length: int = 500,
        with_events: bool = False,
    ) -> List[Dict[str, str]]:
        """Get conversation transcript as list of messages.

        Args:
            limit: Maximum number of interactions to include
            max_statement_length: Maximum length for each statement
            with_events: Whether to include events in transcript

        Returns:
            List of message dictionaries with 'human', 'ai', or 'event' keys
        """
        interactions = await self.get_interactions(limit=limit)
        transcript: List[Dict[str, str]] = []

        for interaction in interactions:
            # Add human message
            utterance = interaction.utterance
            if max_statement_length and len(utterance) > max_statement_length:
                utterance = utterance[:max_statement_length] + "..."
            transcript.append({"human": utterance})

            # Add AI response if present
            if interaction.response:
                response = interaction.response
                if max_statement_length and len(response) > max_statement_length:
                    response = response[:max_statement_length] + "..."
                transcript.append({"ai": response})

            # Add events if requested
            if with_events and interaction.events:
                for event in interaction.events:
                    transcript.append({"event": event})

        return transcript

    async def update_context(self, updates: Dict[str, Any]) -> None:
        """Update conversation context with new values.

        Args:
            updates: Dictionary of context updates to apply
        """
        self.context.update(updates)
        await self.save()

    def data_get(self, key: str) -> Any:
        """Get value from context.

        Args:
            key: Context key to retrieve

        Returns:
            Value from context, or None if not found
        """
        return self.context.get(key)

    def data_set(self, key: str, value: Any) -> None:
        """Set value in context.

        Args:
            key: Context key to set
            value: Value to store
        """
        self.context[key] = value

    async def archive(self) -> None:
        """Archive the conversation."""
        self.status = "archived"
        await self.save()

    async def close(self) -> None:
        """Close the conversation."""
        self.status = "closed"
        await self.save()

    async def get_statistics(self) -> Dict[str, Any]:
        """Get conversation statistics.

        Aggregates metrics from model_log entries across all interactions.

        Returns:
            Dictionary with conversation statistics
        """
        interactions = await self.get_interactions(limit=0)
        total_tokens = 0
        total_duration = 0.0

        for interaction in interactions:
            # Aggregate metrics from model_log entries
            for model_result in interaction.model_log:
                metrics = model_result.get("metrics", {})
                total_tokens += metrics.get("total_tokens", 0)
                total_duration += metrics.get("duration", 0.0)

        return {
            "interaction_count": self.interaction_count,
            "total_tokens": total_tokens,
            "total_duration": total_duration,
            "status": self.status,
            "channel": self.channel,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_interaction_at": (
                self.last_interaction_at.isoformat()
                if self.last_interaction_at
                else None
            ),
        }
