"""Conversation node for managing conversation sessions."""

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from jvspatial.core import Node
from jvspatial.core.annotations import attribute, compound_index
from jvspatial.core.mixins import DeferredSaveMixin

if TYPE_CHECKING:
    from jvagent.memory.interaction import Interaction


@compound_index([("context.user_id", 1), ("context.status", 1)], name="user_status")
@compound_index(
    [("active_tasks.status", 1), ("active_tasks.next_trigger_at", 1)],
    name="active_task_trigger",
)
class Conversation(DeferredSaveMixin, Node):
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

    session_id: str = attribute(
        indexed=True, index_unique=True, default="", description="Session identifier"
    )
    user_id: str = attribute(indexed=True, default="", description="Owning user ID")
    status: str = attribute(
        indexed=True,
        default="active",
        description="Conversation status: active, archived, closed",
    )
    channel: str = attribute(default="default", description="Communication channel")
    created_at: datetime = attribute(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp of creation",
    )
    last_interaction_at: Optional[datetime] = attribute(
        default=None, description="Timestamp of last interaction"
    )
    interaction_count: int = attribute(default=0, description="Number of interactions")
    interaction_limit: int = attribute(
        default=0,
        description="Maximum number of interactions to keep (0 = disabled, no pruning)",
    )
    last_interaction_id: Optional[str] = attribute(
        default=None,
        description="ID of the last interaction in the chain (for optimized access)",
    )
    context: Dict[str, Any] = attribute(
        default_factory=dict, description="Conversation context dictionary"
    )
    active_tasks: List[Dict[str, Any]] = attribute(
        default_factory=list,
        description="Active/inactive tasks for AI context (task tracker)",
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

        When multiple outgoing Interaction edges exist (e.g. after races), picks
        the earliest by ``started_at`` then ``id`` for a stable chain head.

        Returns:
            First Interaction node, or None if no interactions exist
        """
        from jvagent.memory.interaction import Interaction, interaction_sort_key

        interactions = await self.nodes(node=Interaction, direction="out")
        if not interactions:
            return None
        interactions.sort(key=interaction_sort_key)
        return interactions[0]

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

    async def add_interaction(
        self,
        interaction: Optional["Interaction"] = None,
        *,
        utterance: Optional[str] = None,
        channel: Optional[str] = None,
        session_id: str = "",
    ) -> "Interaction":
        """Add an Interaction to the chain with bidirectional edges.

        Interactions are chained chronologically: Interaction1 <-> Interaction2 <-> Interaction3
        The conversation connects to the first interaction only. When utterance is
        provided (and interaction is None), the Interaction is created before connecting.

        Uses a per-conversation lock to prevent concurrent callers from creating
        duplicate first-interaction edges or forking the chain.

        Args:
            interaction: Interaction node to add (optional if utterance provided)
            utterance: User input text (required when interaction is None)
            channel: Communication channel (defaults to conversation's channel)
            session_id: Session identifier for the interaction

        Returns:
            The added Interaction node

        Raises:
            ValueError: If neither interaction nor utterance is provided
        """
        from jvagent.memory.distributed_conversation_lock import (
            conversation_mutation_lock,
        )

        async with conversation_mutation_lock(self.id):
            return await self._add_interaction_unlocked(
                interaction=interaction,
                utterance=utterance,
                channel=channel,
                session_id=session_id,
            )

    async def _add_interaction_unlocked(
        self,
        interaction: Optional["Interaction"] = None,
        utterance: Optional[str] = None,
        channel: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> "Interaction":
        from jvagent.memory.interaction import Interaction

        if interaction is None and utterance is None:
            raise ValueError("Must provide either interaction or utterance")

        from jvagent.core.app import App

        app = await App.get()
        now = await app.now() if app else datetime.now(timezone.utc)

        if interaction is None:
            interaction = await Interaction.create(
                conversation_id=self.id,
                user_id=self.user_id,
                utterance=utterance or "",
                channel=channel or self.channel,
                session_id=session_id,
                started_at=now,
            )

        last_interaction = await self.get_last_interaction()

        if last_interaction:
            await last_interaction.connect(interaction, direction="both")
        else:
            await self.connect(interaction, direction="out")

        self.last_interaction_id = interaction.id
        self.interaction_count += 1
        self.last_interaction_at = now
        await self.save()

        agent = await self.get_agent()
        if (
            agent
            and hasattr(agent, "interaction_limit")
            and agent.interaction_limit > 0
            and self.interaction_limit != agent.interaction_limit
        ):
            self.interaction_limit = agent.interaction_limit
            await self.save()

        if (
            self.interaction_limit > 0
            and self.interaction_count > self.interaction_limit
        ):
            await self._prune_old_interactions()

        return interaction

    async def _prune_old_interactions(self) -> int:
        """Prune interactions outside the rolling window limit.

        Removes the oldest interactions when the count exceeds interaction_limit.
        Only runs if interaction_limit > 0.

        Returns:
            Number of interactions removed.
        """
        if self.interaction_limit <= 0:
            return 0

        # Count how many to remove
        to_remove = self.interaction_count - self.interaction_limit
        if to_remove <= 0:
            return 0

        # Start from the first interaction and remove the oldest ones
        current = await self.get_first_interaction()
        removed = 0

        while current and removed < to_remove:
            next_interaction = await current.get_next_interaction()

            # If there's no next interaction, stop pruning -- removing the last
            # interaction would leave the conversation in an inconsistent state.
            if not next_interaction:
                break

            if await self.is_connected_to(current):
                await self.disconnect(current)
            await self.connect(next_interaction, direction="out")

            if await current.is_connected_to(next_interaction):
                await current.disconnect(next_interaction)

            await current.delete()
            removed += 1
            self.interaction_count -= 1

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
        return removed

    async def create_interaction(
        self,
        utterance: str,
        channel: Optional[str] = None,
        session_id: str = "",
    ) -> "Interaction":
        """Create a new Interaction and connect it to this Conversation.

        Args:
            utterance: User's input text
            channel: Optional channel override (defaults to conversation's channel)
            session_id: Session identifier for this interaction

        Returns:
            Newly created and connected Interaction node
        """
        return await self.add_interaction(
            utterance=utterance,
            channel=channel or self.channel,
            session_id=session_id,
        )

    async def get_interactions(
        self, limit: int = 0, reverse: bool = False
    ) -> List["Interaction"]:
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
                # Strictly enforce limit: break immediately when limit is reached
                if limit > 0 and len(interactions) >= limit:
                    break
                current = await current.get_previous_interaction()
        else:
            # Start from first interaction and traverse forward
            current = await self.get_first_interaction()
            while current:
                interactions.append(current)
                # Strictly enforce limit: break immediately when limit is reached
                if limit > 0 and len(interactions) >= limit:
                    break
                current = await current.get_next_interaction()

        # Invariant: never exceed limit (defensive)
        if limit > 0 and len(interactions) > limit:
            interactions = interactions[:limit]

        return interactions

    @staticmethod
    async def truncate_statement(
        content: str,
        max_length: Optional[int] = None,
        keep_last: bool = False,
        interaction: Optional["Interaction"] = None,
    ) -> str:
        """Truncate a statement (utterance or response) if it exceeds max_length.

        Reusable helper method for truncating statements consistently across the codebase.
        If max_length is None and interaction is provided, attempts to retrieve the agent's
        max_statement_length setting. Used by Conversation methods and accessible via Interaction
        for truncation operations.

        Args:
            content: The content string to truncate
            max_length: Optional maximum length. If None and interaction is provided, will attempt
                to use agent's max_statement_length. None = no truncation.
            keep_last: If True, keep the last N characters (prepend "..."). If False,
                keep the first N characters (append "..."). Default: False.
            interaction: Optional Interaction instance. If provided and max_length is None,
                will attempt to get agent's max_statement_length from the interaction's agent.

        Returns:
            Truncated content string (with "..." prepended/appended if truncated) or original content
        """
        # If max_length not provided, try to get from agent via interaction
        if max_length is None and interaction:
            agent = await interaction.get_agent()
            if agent and hasattr(agent, "max_statement_length"):
                max_length = agent.max_statement_length

        if max_length and len(content) > max_length:
            if keep_last:
                return "..." + content[-max_length:]
            return content[:max_length] + "..."
        return content

    async def _format_interactions(
        self,
        interactions: List["Interaction"],
        with_utterance: bool = True,
        with_response: bool = True,
        with_interpretation: bool = False,
        with_event: bool = False,
        with_posture: bool = False,
        max_statement_length: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Format interactions for language model consumption.

        Utility method that converts interactions into role/content pairs for language models.
        Can be used to wrap any getter method's raw output.

        Args:
            interactions: List of Interaction nodes to format
            with_utterance: If True, include user utterances as user messages
            with_response: If True, include AI responses as assistant messages
            with_interpretation: If True, include interpretations as system messages
            with_event: If True, include events as system messages
            with_posture: If True, prepend SUPPRESS/DEFER system messages when response_posture is set
            max_statement_length: Optional maximum length for utterance and response strings.
                If provided and content exceeds this length, it will be truncated with "..." appended.
                Does not apply to interpretations or events. Default: None (no truncation).
                If None, will attempt to use agent's max_statement_length from each interaction.

        Returns:
            List of dictionaries with 'role' and 'content' keys formatted for language models
        """
        history: List[Dict[str, Any]] = []

        for interaction in interactions:
            # Add interpretation as system message (if present and requested)
            # Note: interpretations are not truncated
            if with_interpretation and interaction.interpretation:
                content = interaction.interpretation
                history.append(
                    {
                        "role": "system",
                        "content": f"[INTERPRETATION] {content}",
                    }
                )

            # Add posture context (if requested and set) - explains why no assistant reply followed
            if with_posture and getattr(interaction, "response_posture", None):
                posture = interaction.response_posture
                utterance = interaction.utterance or ""
                truncated = await Conversation.truncate_statement(
                    utterance, max_statement_length, interaction=interaction
                )
                if posture == "SUPPRESS":
                    history.append(
                        {
                            "role": "system",
                            "content": f'[SUPPRESSED] User said: "{truncated}"',
                        }
                    )
                elif posture == "DEFER":
                    history.append(
                        {
                            "role": "system",
                            "content": f'[DEFERRED] User said: "{truncated}"',
                        }
                    )

            # Add user utterance (if requested) - truncated if max_statement_length is set
            if with_utterance:
                truncated_utterance = await Conversation.truncate_statement(
                    interaction.utterance, max_statement_length, interaction=interaction
                )
                history.append(
                    {
                        "role": "user",
                        "content": truncated_utterance,
                    }
                )

            # Add assistant: optional transient canned lead-in, then main response
            if with_response:
                canned_str = (
                    getattr(interaction, "canned_response", None) or ""
                ).strip()
                response_str = (interaction.response or "").strip()
                if canned_str and not (
                    response_str and response_str.startswith(canned_str)
                ):
                    truncated_canned = await Conversation.truncate_statement(
                        canned_str, max_statement_length, interaction=interaction
                    )
                    history.append(
                        {
                            "role": "assistant",
                            "content": truncated_canned,
                        }
                    )
                if interaction.response:
                    truncated_response = await Conversation.truncate_statement(
                        interaction.response,
                        max_statement_length,
                        interaction=interaction,
                    )
                    history.append(
                        {
                            "role": "assistant",
                            "content": truncated_response,
                        }
                    )

            # Add events as system messages (if present and requested)
            # Note: events are not truncated
            if with_event and interaction.events:
                for event in interaction.events:
                    # Extract content from event dict structure
                    if isinstance(event, dict):
                        event_str = event.get("content", str(event))
                    else:
                        event_str = str(event)
                    history.append(
                        {
                            "role": "system",
                            "content": f"[EVENT] {event_str}",
                        }
                    )

        return history

    async def get_interaction_history(
        self,
        limit: int = 10,
        excluded: Union[str, List[str], bool] = False,
        with_utterance: bool = True,
        with_response: bool = True,
        with_interpretation: bool = False,
        with_event: bool = False,
        with_posture: bool = False,
        formatted: bool = True,
        max_statement_length: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Get interaction history with configurable element inclusion and formatting.

        Unified utility method for retrieving interaction history with fine-grained control
        over which elements to include and whether to format for language models.

        Args:
            limit: Maximum number of interactions to include (most recent). The limit is
                applied to the number of interactions fetched, then with_xxx flags control
                which fields are included in the output for each interaction.
            excluded: Interaction ID(s) to exclude from results. Can be a single string,
                a list of strings, or False (default) for no exclusion.
            with_utterance: If True, include user utterances (default: True)
            with_response: If True, include AI responses (default: True)
            with_interpretation: If True, include interpretations (default: False)
            with_event: If True, include events (default: False)
            with_posture: If True, include response_posture (SUPPRESS/DEFER) as system messages
            formatted: If True, format as role/content pairs for language models.
                If False, return raw format with metadata. Default: True.
            max_statement_length: Optional maximum length for utterance and response strings.
                If provided and content exceeds this length, it will be truncated with "..." appended.
                Does not apply to interpretations or events. Default: None (no truncation).

        Returns:
            If formatted=True: List of dictionaries with 'role' and 'content' keys
            If formatted=False: List of dictionaries with selected elements and metadata

        Note:
            The with_xxx flags control field inclusion, not interaction filtering.
            All interactions within the limit are included in results; interactions
            without requested fields will have those fields omitted from the entry.
        """
        # Normalize excluded to a set of IDs for efficient lookup
        excluded_ids: set = set()
        if excluded:
            if isinstance(excluded, str):
                excluded_ids.add(excluded)
            elif isinstance(excluded, list):
                excluded_ids.update(excluded)

        # Fetch enough interactions to account for exclusions, ensuring we get exactly 'limit' non-excluded ones
        # If we need to exclude some, fetch extra to compensate
        fetch_limit = limit + len(excluded_ids) if excluded_ids else limit

        # Get most recent interactions (reverse=True gives newest first)
        interactions = await self.get_interactions(
            limit=fetch_limit if fetch_limit > 0 else 0, reverse=True
        )

        # Filter out excluded interactions if specified
        if excluded_ids:
            interactions = [i for i in interactions if i.id not in excluded_ids]

        # Strictly limit to requested number (defensive: ensure we never exceed limit)
        # This handles cases where get_interactions might return more than requested
        interactions = interactions[:limit]

        # Reverse to chronological order (oldest first)
        interactions.reverse()

        if formatted:
            # Use formatter utility
            return await self._format_interactions(
                interactions,
                with_utterance=with_utterance,
                with_response=with_response,
                with_interpretation=with_interpretation,
                with_event=with_event,
                with_posture=with_posture,
                max_statement_length=max_statement_length,
            )
        else:
            # Raw format with selected elements
            history: List[Dict[str, Any]] = []
            for interaction in interactions:
                entry: Dict[str, Any] = {
                    "interaction_id": interaction.id,
                    "started_at": (
                        interaction.started_at.isoformat()
                        if interaction.started_at
                        else None
                    ),
                }

                if with_utterance:
                    entry["utterance"] = await Conversation.truncate_statement(
                        interaction.utterance,
                        max_statement_length,
                        interaction=interaction,
                    )

                if with_response and interaction.response:
                    entry["response"] = await Conversation.truncate_statement(
                        interaction.response,
                        max_statement_length,
                        interaction=interaction,
                    )

                if with_interpretation and interaction.interpretation:
                    # Note: interpretations are not truncated
                    entry["interpretation"] = interaction.interpretation
                    if interaction.anchors:
                        entry["anchors"] = interaction.anchors

                if with_event and interaction.events:
                    # Note: events are not truncated
                    entry["events"] = interaction.events

                if with_posture and getattr(interaction, "response_posture", None):
                    entry["response_posture"] = interaction.response_posture

                history.append(entry)

            return history

    async def get_conversation_history(
        self,
        limit: int = 10,
        excluded: Union[str, List[str], bool] = False,
        formatted: bool = True,
        max_statement_length: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Get formatted conversation history (utterance and response pairs).

        Args:
            limit: Maximum number of interactions to include (most recent)
            excluded: Interaction ID(s) to exclude from results. Can be a single string,
                a list of strings, or False (default) for no exclusion.
            formatted: If True, format as role/content pairs for language models.
                If False, return raw format with 'utterance' and 'response' keys. Default: True.
            max_statement_length: Optional maximum length for utterance and response strings.
                If provided and content exceeds this length, it will be truncated with "..." appended.
                Does not apply to interpretations or events. Default: None (no truncation).

        Returns:
            If formatted=True: List of dictionaries with 'role' and 'content' keys
            If formatted=False: List of dictionaries with 'utterance' and optional 'response' keys
        """
        return await self.get_interaction_history(
            limit=limit,
            excluded=excluded,
            with_utterance=True,
            with_response=True,
            with_interpretation=False,
            with_event=False,
            formatted=formatted,
            max_statement_length=max_statement_length,
        )

    async def get_event_history(
        self,
        limit: int = 10,
        excluded: Union[str, List[str], bool] = False,
        formatted: bool = True,
        max_statement_length: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Get formatted event history from interactions.

        Args:
            limit: Maximum number of interactions to include (most recent)
            excluded: Interaction ID(s) to exclude from results. Can be a single string,
                a list of strings, or False (default) for no exclusion.
            formatted: If True, format as role/content pairs for language models.
                If False, return raw format with metadata. Default: True.
            max_statement_length: Optional maximum length for utterance and response strings.
                If provided and content exceeds this length, it will be truncated with "..." appended.
                Does not apply to interpretations or events. Default: None (no truncation).
                Note: This parameter has no effect when retrieving only events.

        Returns:
            If formatted=True: List of dictionaries with 'role' and 'content' keys
            If formatted=False: List of dictionaries with 'interaction_id', 'started_at', and 'events' keys
        """
        return await self.get_interaction_history(
            limit=limit,
            excluded=excluded,
            with_utterance=False,
            with_response=False,
            with_interpretation=False,
            with_event=True,
            formatted=formatted,
            max_statement_length=max_statement_length,
        )

    async def get_interpretation_history(
        self,
        limit: int = 10,
        excluded: Union[str, List[str], bool] = False,
        formatted: bool = True,
        max_statement_length: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Get formatted interpretation history from interactions.

        Args:
            limit: Maximum number of interactions to include (most recent)
            excluded: Interaction ID(s) to exclude from results. Can be a single string,
                a list of strings, or False (default) for no exclusion.
            formatted: If True, format as role/content pairs for language models.
                If False, return raw format with metadata. Default: True.
            max_statement_length: Optional maximum length for utterance and response strings.
                If provided and content exceeds this length, it will be truncated with "..." appended.
                Does not apply to interpretations or events. Default: None (no truncation).
                Note: This parameter has no effect when retrieving only interpretations.

        Returns:
            If formatted=True: List of dictionaries with 'role' and 'content' keys
            If formatted=False: List of dictionaries with 'interaction_id', 'started_at', 'interpretation',
            and 'anchors' keys
        """
        return await self.get_interaction_history(
            limit=limit,
            excluded=excluded,
            with_utterance=False,
            with_response=False,
            with_interpretation=True,
            with_event=False,
            formatted=formatted,
            max_statement_length=max_statement_length,
        )

    async def get_context_history(
        self,
        limit: int = 10,
        excluded: Union[str, List[str], bool] = False,
        formatted: bool = True,
        max_statement_length: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Get formatted context history combining conversation, events, and interpretations.

        Args:
            limit: Maximum number of interactions to include (most recent)
            excluded: Interaction ID(s) to exclude from results. Can be a single string,
                a list of strings, or False (default) for no exclusion.
            formatted: If True, format as role/content pairs for language models.
                Includes user utterance, AI response, events, and interpretations. Default: True.
            max_statement_length: Optional maximum length for utterance and response strings.
                If provided and content exceeds this length, it will be truncated with "..." appended.
                Does not apply to interpretations or events. Default: None (no truncation).

        Returns:
            If formatted=True: List of dictionaries with 'role' and 'content' keys
                (includes user, assistant, system messages for interpretations and events)
            If formatted=False: List of dictionaries containing conversation, events,
                and interpretation data with metadata
        """
        return await self.get_interaction_history(
            limit=limit,
            excluded=excluded,
            with_utterance=True,
            with_response=True,
            with_interpretation=True,
            with_event=True,
            formatted=formatted,
            max_statement_length=max_statement_length,
        )

    async def update_context(self, updates: Dict[str, Any]) -> None:
        """Update conversation context with new values.

        Args:
            updates: Dictionary of context updates to apply
        """
        self.context.update(updates)
        await self.save()

    async def add_active_task(
        self,
        description: str,
        metadata: Optional[Dict[str, Any]] = None,
        task_id: Optional[str] = None,
        action_name: Optional[str] = None,
        task_type: Optional[str] = None,
    ) -> None:
        """Add or update an active task (upsert by task_id, action_name, or description).

        Args:
            description: Human/AI-readable task description (action name can be included)
            metadata: Optional metadata (interview_type, current_question, etc.)
            task_id: Optional unique ID; auto-generated UUID when not provided
            action_name: Optional action class name for actions that manage their tasks
            task_type: Optional task type (e.g. 'INTERVIEW') for router routing
        """
        now = datetime.now(timezone.utc).isoformat()
        short_uuid = uuid.uuid4().hex[:12]
        default_tid = (
            f"{action_name}:{short_uuid}" if action_name else f"task_{uuid.uuid4().hex}"
        )
        metadata = metadata or {}
        entry: Dict[str, Any] = {
            "task_id": task_id or default_tid,
            "task_type": task_type,
            "description": description,
            "action_name": action_name,
            "status": "active",
            # Promote trigger fields to top level for indexing
            "next_trigger_at": metadata.get("trigger_time"),
            "trigger_condition": metadata.get("trigger_condition"),
            "metadata": metadata,
            "created_at": now,
            "updated_at": now,
        }

        for i, t in enumerate(self.active_tasks):
            if (
                (task_id and t.get("task_id") == task_id)
                or t.get("description") == description
                or (action_name and t.get("action_name") == action_name)
            ):
                entry["task_id"] = t.get("task_id") or task_id or default_tid
                entry["created_at"] = t.get("created_at", now)
                self.active_tasks[i] = entry
                await self.save()
                return
        self.active_tasks.append(entry)
        await self.save()

        # Fire task created callback (push-based notification)
        try:
            from jvagent.core.callback import trigger_task_created_callback
            await trigger_task_created_callback(self, entry)
        except Exception:
            # Callbacks are non-blocking and shouldn't fail the primary task creation
            pass

    async def update_task(
        self,
        status: str,
        task_id: Optional[str] = None,
        description: Optional[str] = None,
        action_name: Optional[str] = None,
    ) -> bool:
        """Update task status. Preserves task for audit log.

        Args:
            status: New status (e.g. "cancelled", "completed")
            task_id: Task ID for exact match (optional). Use when multiple tasks per action.
            description: Description of task to update (optional)
            action_name: Action class name of task to update (optional)

        Returns:
            True if task was updated, False if not found

        Note:
            Provide at least one of task_id, description, or action_name. When multiple
            tasks exist per action, use task_id or description to distinguish.
        """
        if not task_id and not description and not action_name:
            return False
        now = datetime.now(timezone.utc).isoformat()
        for i, t in enumerate(self.active_tasks):
            if (
                (task_id and t.get("task_id") == task_id)
                or (description and t.get("description") == description)
                or (action_name and t.get("action_name") == action_name)
            ):
                self.active_tasks[i] = {**t, "status": status, "updated_at": now}
                await self.save()
                return True
        return False

    async def remove_active_task(
        self,
        task_id: Optional[str] = None,
        description: Optional[str] = None,
        action_name: Optional[str] = None,
    ) -> bool:
        """Transition task to completed status (preserves task for audit log).

        Delegates to update_task with status="completed".
        Kept for backward compatibility with custom actions.

        Args:
            task_id: Task ID for exact match (optional)
            description: Description of task (optional)
            action_name: Action class name of task (optional)

        Returns:
            True if task was updated, False if not found
        """
        return await self.update_task(
            status="completed",
            task_id=task_id,
            description=description,
            action_name=action_name,
        )

    def get_active_tasks(
        self,
        status: Optional[str] = None,
        action_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get tasks, optionally filtered by status and/or action_name.

        Args:
            status: Optional filter ("active", "inactive", "upcoming")
            action_name: Optional filter by action class name for actions managing tasks

        Returns:
            List of task dicts
        """
        tasks = list(self.active_tasks)
        if status is not None:
            if isinstance(status, list):
                tasks = [t for t in tasks if t.get("status") in status]
            else:
                tasks = [t for t in tasks if t.get("status") == status]
        if action_name is not None:
            tasks = [t for t in tasks if t.get("action_name") == action_name]
        return tasks

    def get_active_task(
        self,
        *,
        task_id: Optional[str] = None,
        task_type: Optional[str] = None,
        description: Optional[str] = None,
        action_name: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get first task matching all provided filters.

        Replaces get_active_task_by_description, get_active_task_by_action,
        and get_active_task_action_name. Use task.get("action_name") when
        you need the action name.

        Args:
            task_id: Optional filter by task_id
            task_type: Optional filter by task_type (e.g. "INTERVIEW")
            description: Optional filter by description
            action_name: Optional filter by action_name
            status: Optional filter by status (e.g. "active")

        Returns:
            First matching task dict, or None
        """
        for t in self.active_tasks:
            if task_id is not None and t.get("task_id") != task_id:
                continue
            if task_type is not None and t.get("task_type") != task_type:
                continue
            if description is not None and t.get("description") != description:
                continue
            if action_name is not None and t.get("action_name") != action_name:
                continue
            if status is not None and t.get("status") != status:
                continue
            return t
        return None

    def get_active_tasks_for_context(self) -> List[str]:
        """Return list of task descriptions for context line.

        Returns:
            List of descriptions for active tasks (status=active)
        """
        return [t["description"] for t in self.get_active_tasks(status="active")]

    async def archive(self) -> None:
        """Archive the conversation."""
        self.status = "archived"
        await self.save()

    async def close(self) -> None:
        """Close the conversation."""
        self.status = "closed"
        await self.save()

    async def delete(self, cascade: bool = True) -> None:
        """Delete this conversation and refresh Memory counters from the graph.

        This override runs when :meth:`delete` is called on a Conversation. Cascade
        deletes from :meth:`User.delete` bypass this override; :meth:`Memory.purge_user_memory`
        refreshes counters after bulk user deletes.

        Args:
            cascade: Whether to cascade deletion to dependent nodes (default: True)
        """
        from jvagent.memory.manager import Memory
        from jvagent.memory.user import User

        user = await self.node(direction="in", node=User)
        memory = None
        if user:
            memory = await user.node(direction="in", node=Memory)

        await super().delete(cascade=cascade)

        if memory:
            await memory.refresh_memory_counters_from_graph()

    async def get_statistics(self) -> Dict[str, Any]:
        """Get conversation statistics.

        Prefers usage when available; falls back to observability_metrics
        aggregation for interactions without usage (backward compatibility).

        Returns:
            Dictionary with conversation statistics
        """
        interactions = await self.get_interactions(limit=0)
        total_tokens = 0
        total_duration = 0.0

        for interaction in interactions:
            if hasattr(interaction, "usage") and interaction.usage:
                total_tokens += interaction.usage.get("total_tokens", 0)
                total_duration += interaction.usage.get("total_duration_seconds", 0.0)
            elif (
                hasattr(interaction, "observability_metrics")
                and interaction.observability_metrics
            ):
                for event in interaction.observability_metrics:
                    if event.get("event_type") in ("model_call", "embedding_call"):
                        event_data = event.get("data", {})
                        usage = event_data.get("usage", {})
                        total_tokens += usage.get("total_tokens", 0)
                        duration = event_data.get("duration", 0.0)
                        if duration:
                            total_duration += duration

        active_task_count = len(self.get_active_tasks(status="active"))

        return {
            "interaction_count": self.interaction_count,
            "total_tokens": total_tokens,
            "total_duration": total_duration,
            "active_task_count": active_task_count,
            "status": self.status,
            "channel": self.channel,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_interaction_at": (
                self.last_interaction_at.isoformat()
                if self.last_interaction_at
                else None
            ),
        }
