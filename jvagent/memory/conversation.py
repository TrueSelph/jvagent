"""Conversation node for managing conversation sessions."""

from datetime import datetime, timezone
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
        - Connected to first Interaction via outgoing edge
        - Interactions are chained: Interaction1 <-> Interaction2 <-> Interaction3
          (bidirectional edges allow forward and backward traversal)

    Cascade Delete Behavior:
        - Deleting a Conversation cascades to all chained Interactions

    Attributes:
        session_id: Session identifier (can be set or auto-generated)
        user_id: Owning user's identifier
        status: Conversation status (active, archived, closed)
        channel: Communication channel
        created_at: Timestamp of conversation creation
        last_interaction_at: Timestamp of last interaction
        interaction_count: Number of interactions in this conversation
        interaction_limit: Maximum number of interactions to keep (0 = disabled, no pruning)
        context: Conversation context dictionary for storing state
    """

    session_id: str = attribute(default="", description="Session identifier")
    user_id: str = attribute(default="", description="Owning user ID")
    status: str = attribute(
        default="active", description="Conversation status: active, archived, closed"
    )
    channel: str = attribute(default="default", description="Communication channel")
    created_at: datetime = attribute(
        default_factory=lambda: datetime.now(timezone.utc), description="Timestamp of creation"
    )
    last_interaction_at: Optional[datetime] = attribute(
        default=None, description="Timestamp of last interaction"
    )
    interaction_count: int = attribute(
        default=0, description="Number of interactions"
    )
    interaction_limit: int = attribute(
        default=0, description="Maximum number of interactions to keep (0 = disabled, no pruning)"
    )
    context: Dict[str, Any] = attribute(
        default_factory=dict, description="Conversation context dictionary"
    )

    async def get_first_interaction(self) -> Optional["Interaction"]:
        """Get the first interaction in the chain.

        Returns:
            First Interaction node, or None if no interactions exist
        """
        from jvagent.memory.interaction import Interaction

        # Get interactions connected directly from conversation (first interaction only)
        interactions = await self.nodes(node=Interaction, direction="out")
        return interactions[0] if interactions else None

    async def get_last_interaction(self) -> Optional["Interaction"]:
        """Get the last interaction in the chain by traversing forward.

        Returns:
            Last Interaction node, or None if no interactions exist
        """
        first = await self.get_first_interaction()
        if not first:
            return None

        # Traverse forward through the chain
        current = first
        while True:
            next_interaction = await current.get_next_interaction()
            if not next_interaction:
                return current
            current = next_interaction

    async def add_interaction(self, interaction: "Interaction") -> "Interaction":
        """Add an Interaction to the chain with bidirectional edges.

        Interactions are chained chronologically: Interaction1 <-> Interaction2 <-> Interaction3
        The conversation connects to the first interaction only.

        Args:
            interaction: Interaction node to add

        Returns:
            The added Interaction node
        """
        from jvagent.memory.interaction import Interaction

        last_interaction = await self.get_last_interaction()

        if last_interaction:
            # Chain the new interaction after the last one (bidirectional edge)
            await last_interaction.connect(interaction, direction="both")
        else:
            # This is the first interaction - connect conversation to it
            await self.connect(interaction, direction="out")

        self.interaction_count += 1
        self.last_interaction_at = datetime.now(timezone.utc)
        await self.save()

        # Apply rolling window pruning if limit is set and exceeded
        if self.interaction_limit > 0 and self.interaction_count > self.interaction_limit:
            await self._prune_old_interactions()

        return interaction

    async def _prune_old_interactions(self) -> None:
        """Prune interactions outside the rolling window limit.

        Removes the oldest interactions when the count exceeds interaction_limit.
        Only runs if interaction_limit > 0.
        """
        if self.interaction_limit <= 0:
            return

        # Count how many to remove
        to_remove = self.interaction_count - self.interaction_limit
        if to_remove <= 0:
            return

        # Start from the first interaction and remove the oldest ones
        current = await self.get_first_interaction()
        removed = 0

        while current and removed < to_remove:
            next_interaction = await current.get_next_interaction()

            # Disconnect from conversation if this is the first interaction
            if removed == 0:
                if await self.is_connected_to(current):
                    await self.disconnect(current)
                # If there's a next interaction, connect conversation to it (new first)
                if next_interaction:
                    await self.connect(next_interaction, direction="out")

            # Disconnect from next interaction if it exists
            if next_interaction:
                if await current.is_connected_to(next_interaction):
                    await current.disconnect(next_interaction)

            # Delete the interaction
            await current.delete()
            removed += 1
            self.interaction_count -= 1

            # Move to next
            current = next_interaction

        await self.save()

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

    async def get_interactions(self, limit: int = 0, reverse: bool = False) -> List["Interaction"]:
        """Get Interactions by traversing the chain in chronological order.

        Args:
            limit: Maximum number of interactions to return (0 for all)
            reverse: If True, return in reverse chronological order (newest first)

        Returns:
            List of Interaction nodes in chronological order (oldest first by default)
        """
        from jvagent.memory.interaction import Interaction

        interactions: List[Interaction] = []
        
        if reverse:
            # Start from last interaction and traverse backward
            current = await self.get_last_interaction()
            while current:
                interactions.append(current)
                if limit > 0 and len(interactions) >= limit:
                    break
                current = await current.get_previous_interaction()
        else:
            # Start from first interaction and traverse forward
            current = await self.get_first_interaction()
            while current:
                interactions.append(current)
                if limit > 0 and len(interactions) >= limit:
                    break
                current = await current.get_next_interaction()

        return interactions

    async def get_transcript(
        self,
        limit: int = 10,
        max_statement_length: int = 500,
        with_events: bool = False,
    ) -> List[Dict[str, str]]:
        """Get conversation transcript as list of messages.

        Args:
            limit: Maximum number of interactions to include (most recent)
            max_statement_length: Maximum length for each statement
            with_events: Whether to include events in transcript

        Returns:
            List of message dictionaries with 'human', 'ai', or 'event' keys
        """
        # Get most recent interactions (reverse=True gives newest first)
        interactions = await self.get_interactions(limit=limit, reverse=True)
        # Reverse to get chronological order (oldest first) for transcript
        interactions.reverse()
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
