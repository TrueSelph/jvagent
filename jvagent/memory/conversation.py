"""Conversation node for managing conversation sessions."""

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.core import Node
from jvspatial.core.annotations import attribute, compound_index

if TYPE_CHECKING:
    from jvagent.memory.interaction import Interaction


@compound_index([("context.user_id", 1), ("context.status", 1)], name="user_status")
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

    session_id: str = attribute(indexed=True, index_unique=True, default="", description="Session identifier")
    user_id: str = attribute(indexed=True, default="", description="Owning user ID")
    status: str = attribute(
        indexed=True, default="active", description="Conversation status: active, archived, closed"
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
    last_interaction_id: Optional[str] = attribute(
        default=None, description="ID of the last interaction in the chain (for optimized access)"
    )
    context: Dict[str, Any] = attribute(
        default_factory=dict, description="Conversation context dictionary"
    )

    async def get_agent(self) -> Optional[Any]:
        """Get the Agent node this Conversation belongs to.

        Traverses: Conversation -> User (incoming edge) -> Memory -> Agent.

        Returns:
            Agent instance if found, None otherwise
        """
        from jvagent.memory.user import User

        # Get User node (Conversation is connected to User via incoming edge)
        user = await self.node(direction="in", node=User)
        if user:
            # Get Agent from User using its get_agent() method
            return await user.get_agent()
        return None

    async def get_first_interaction(self) -> Optional["Interaction"]:
        """Get the first interaction in the chain.

        Returns:
            First Interaction node, or None if no interactions exist
        """
        from jvagent.memory.interaction import Interaction

        # Get interactions connected directly from conversation (first interaction only)
        interactions = await self.nodes(node=Interaction, direction="out")
        return interactions[0] if interactions else None

    async def _find_last_interaction(self) -> Optional["Interaction"]:
        """Find the last interaction by traversing the chain (used when reference is stale).

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

    async def get_last_interaction(self) -> Optional["Interaction"]:
        """Get the last interaction in the chain using cached reference.

        Returns:
            Last Interaction node, or None if no interactions exist
        """
        from jvagent.memory.interaction import Interaction

        if not self.last_interaction_id:
            # No cached reference, try to find by traversal
            last = await self._find_last_interaction()
            if last:
                # Cache the reference for future use
                self.last_interaction_id = last.id
                await self.save()
            return last

        # Directly access the last interaction using the cached reference
        last_interaction = await Interaction.get(self.last_interaction_id)
        
        # If the reference is stale (interaction was deleted), rebuild by traversal
        if not last_interaction:
            last = await self._find_last_interaction()
            if last:
                # Update the reference
                self.last_interaction_id = last.id
                await self.save()
                return last
            else:
                # No interactions exist
                self.last_interaction_id = None
                await self.save()
                return None

        return last_interaction

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

        # Update the last interaction reference
        self.last_interaction_id = interaction.id
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

        # Update last_interaction_id reference after pruning
        # The last interaction should still be valid (we only remove from the start)
        # But verify it still exists
        if self.last_interaction_id:
            from jvagent.memory.interaction import Interaction
            last = await Interaction.get(self.last_interaction_id)
            if not last:
                # Reference is stale, rebuild it by traversal
                last = await self._find_last_interaction()
                if last:
                    self.last_interaction_id = last.id
                else:
                    self.last_interaction_id = None

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

    async def delete(self, cascade: bool = True) -> None:
        """Delete this conversation and update Memory's total_conversations counter.

        Args:
            cascade: Whether to cascade deletion to dependent nodes (default: True)
        """
        from jvagent.memory.manager import Memory
        from jvagent.memory.user import User

        # Get Memory node to update counter before deletion
        user = await self.node(direction="in", node=User)
        if user:
            memory = await user.node(direction="in", node=Memory)
            if memory:
                memory.total_conversations = max(0, memory.total_conversations - 1)
                await memory.save()

        # Call parent delete to perform actual deletion
        await super().delete(cascade=cascade)

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
