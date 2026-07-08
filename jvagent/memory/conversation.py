"""Conversation node for managing conversation sessions."""

import logging
import os
import secrets
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from jvspatial.core import Node
from jvspatial.core.annotations import attribute, compound_index
from jvspatial.core.mixins import DeferredSaveMixin

if TYPE_CHECKING:
    from jvagent.memory.interaction import Interaction

logger = logging.getLogger(__name__)


# Compound field names are model fields (``session_id``); jvspatial maps them to
# ``context.<name>`` in MongoDB. A ``context.`` prefix on the model field becomes
# ``context.context.*``.
@compound_index(
    [("session_id", 1)],
    name="conversation_session_id",
    unique=True,
    partial_filter_expression={
        "context.session_id": {"$gt": ""},
        "context.status": {"$gt": ""},
    },
)
@compound_index(
    [("user_id", 1), ("status", 1)],
    name="user_status",
)
@compound_index(
    [("tasks.status", 1), ("tasks.created_at", 1)],
    name="task_status_created",
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

    session_id: str = attribute(default="", description="Session identifier")
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
    memory: Dict[str, str] = attribute(
        default_factory=dict,
        description=(
            "Conversation-scoped general-purpose memory: a flat key→markdown map "
            "for working notes that should persist across interactions in this "
            "conversation but not beyond it. Auto-cleaned with the conversation."
        ),
    )
    memory_tags: Dict[str, List[str]] = attribute(
        default_factory=dict,
        description="Tags per memory key (key→list[str]) for filtering and retrieval.",
    )
    tasks: List[Dict[str, Any]] = attribute(
        default_factory=list,
        description="Tasks for this conversation (task tracker)",
    )
    prune_artifacts_with_interaction: bool = attribute(
        default=True,
        description=(
            "When True (default), pruning an interaction reaps the artifacts it "
            "solely produced — refcounted; pinned artifacts are exempt (ADR-0021)."
        ),
    )
    token_secret: str = attribute(
        default="",
        description=(
            "Per-conversation secret bound into anonymous session capability "
            "tokens (ADR-0020). Rotating it revokes any outstanding token for "
            "this conversation. Empty until the first token is minted; backfilled "
            "lazily on resume for pre-existing conversations."
        ),
    )

    def ensure_token_secret(self) -> str:
        """Return this conversation's token secret, minting one if absent.

        Used by the session-token guard (ADR-0020) to bind a capability token to
        this specific conversation. Caller is responsible for ``save()`` — this
        only mutates the in-memory attribute so the mint + persist happen in the
        same unit of work as conversation creation/resume.
        """
        if not self.token_secret:
            self.token_secret = secrets.token_urlsafe(32)
        return self.token_secret

    def rotate_token_secret(self) -> str:
        """Rotate the token secret, invalidating outstanding tokens (ADR-0020).

        Caller is responsible for ``save()``.
        """
        self.token_secret = secrets.token_urlsafe(32)
        return self.token_secret

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
            holds_conversation_mutation_lock,
        )

        if holds_conversation_mutation_lock(self.id):
            return await self._add_interaction_unlocked(
                interaction=interaction,
                utterance=utterance,
                channel=channel,
                session_id=session_id,
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

        agent = await self.get_agent()
        if (
            agent
            and hasattr(agent, "interaction_limit")
            and agent.interaction_limit > 0
            and self.interaction_limit != agent.interaction_limit
        ):
            self.interaction_limit = agent.interaction_limit

        # AUDIT-memory HIGH-10: a single save() covers all the field
        # changes above instead of two back-to-back writes.
        await self.save()

        if (
            self.interaction_limit > 0
            and self.interaction_count > self.interaction_limit
        ):
            await self._prune_old_interactions()

        return interaction

    # ------------------------------------------------------------------
    # Artifact memory (ADR-0021): a Conversation-scoped registry branch node
    # holding Artifact nodes, associated to producing Interactions via a generic
    # edge. Lifecycle is refcounted to those interactions (see _reap_artifacts_for).
    # ------------------------------------------------------------------

    async def _get_or_create_artifacts(self) -> Any:
        """The conversation's single ``Artifacts`` registry node (lazy)."""
        from jvagent.memory.artifact import Artifacts

        existing = await self.nodes(node=Artifacts, direction="out")
        if existing:
            return existing[0]
        branch = await Artifacts.create()
        await self.connect(branch, direction="out")
        return branch

    async def add_artifact(
        self,
        interaction: Optional[Any] = None,
        *,
        name: str,
        data: str = "",
        summary: str = "",
        tags: Optional[List[str]] = None,
        source: str = "",
        kind: str = "text",
        pinned: bool = False,
        filename: str = "",
        mime: str = "",
        size: int = 0,
        path: str = "",
    ) -> Any:
        """Create an ``Artifact`` in the registry and associate it to ``interaction``.

        ``filename``/``mime``/``size``/``path`` describe a file-backed artifact
        (ADR-0021 S4) whose bytes live in storage at ``path`` (not inline). The
        bytes are reaped with the artifact via ``_reap_artifacts_for``.

        Re-referencing an existing artifact from another interaction should add a
        ``PRODUCED`` edge (call again with the same ``name`` resolved externally,
        or use ``associate_artifact``) rather than duplicating.
        """
        from jvagent.memory.artifact import Artifact

        branch = await self._get_or_create_artifacts()
        artifact = await Artifact.create(
            name=name,
            data=data,
            summary=summary,
            tags=list(tags or []),
            source=source,
            kind=kind,
            pinned=pinned,
            filename=filename,
            mime=mime,
            size=int(size or 0),
            path=path,
        )
        await branch.connect(artifact, direction="out")  # registry membership
        if interaction is not None:
            await interaction.connect(artifact, direction="out")  # PRODUCED
        return artifact

    async def get_artifacts(
        self,
        *,
        name: Optional[str] = None,
        source: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> List[Any]:
        """All registry artifacts, optionally filtered by name / source / any tag."""
        from jvagent.memory.artifact import Artifact, Artifacts

        branches = await self.nodes(node=Artifacts, direction="out")
        if not branches:
            return []
        items = await branches[0].nodes(node=Artifact, direction="out")
        want_tags = set(tags or [])
        out: List[Any] = []
        for a in items:
            if name is not None and a.name != name:
                continue
            if source is not None and a.source != source:
                continue
            if want_tags and not (want_tags & set(a.tags or [])):
                continue
            out.append(a)
        return out

    async def _reap_artifacts_for(self, interaction: Any) -> int:
        """Refcounted cascade for a pruned ``interaction`` (ADR-0021).

        Drop the interaction's ``PRODUCED`` edges; delete each artifact it solely
        produced (no other live producing Interaction) unless ``pinned``. Shared
        artifacts survive until their last producer is pruned. Best-effort.
        """
        from jvagent.memory.artifact import Artifact, Artifacts
        from jvagent.memory.interaction import Interaction

        try:
            produced = await interaction.nodes(node=Artifact, direction="out")
        except Exception:
            return 0
        if not produced:
            return 0
        branches = await self.nodes(node=Artifacts, direction="out")
        branch = branches[0] if branches else None
        reaped = 0
        for artifact in produced:
            try:
                await interaction.disconnect(artifact)
                remaining = await artifact.nodes(node=Interaction, direction="in")
            except Exception:
                continue
            if remaining or getattr(artifact, "pinned", False):
                continue
            try:
                if branch is not None:
                    await branch.disconnect(artifact)
                await self._delete_artifact_file(artifact)
                await artifact.delete()
                reaped += 1
            except Exception:
                pass
        return reaped

    async def _delete_artifact_file(self, artifact: Any) -> None:
        """Best-effort delete of a file-backed artifact's stored bytes (S4).

        File-backed artifacts keep their bytes in storage (not on the node), so
        the storage object must be removed alongside the node or it orphans.
        Silent on any failure — pruning must never raise.
        """
        path = (getattr(artifact, "path", "") or "").strip()
        if not path:
            return
        try:
            from jvagent.core.app import App

            app = await App.get()
            if app is not None:
                await app.delete_file(path)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("artifact file cleanup failed for %s: %s", path, exc)

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

        # Cap work per call so append latency stays bounded when far over limit
        # (e.g. after lowering interaction_limit). Further pruning happens on
        # later appends or via Memory.apply_interaction_limit_pruning_for_connected_users.
        # AUDIT-memory MED-02: explicitly reject zero/negative env values so a
        # misconfigured ``JVAGENT_MAX_INTERACTIONS_PRUNED_PER_CALL=-1`` does
        # not silently disable pruning (the previous ``max(1, …)`` swallowed
        # it without a log).
        raw_env = os.environ.get("JVAGENT_MAX_INTERACTIONS_PRUNED_PER_CALL", "100")
        try:
            parsed_cap = int(raw_env)
        except ValueError:
            logger.warning(
                "JVAGENT_MAX_INTERACTIONS_PRUNED_PER_CALL is not an int (%r); "
                "falling back to default 100",
                raw_env,
            )
            parsed_cap = 100
        if parsed_cap < 1:
            logger.warning(
                "JVAGENT_MAX_INTERACTIONS_PRUNED_PER_CALL must be >= 1 (got %d); "
                "clamping to 1",
                parsed_cap,
            )
            parsed_cap = 1
        max_prune = parsed_cap

        # Start from the first interaction and remove the oldest ones
        current = await self.get_first_interaction()
        removed = 0

        while current and removed < to_remove and removed < max_prune:

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

            # Refcounted artifact cascade before the interaction node is gone
            # (ADR-0021): reap artifacts this interaction solely produced.
            if self.prune_artifacts_with_interaction:
                try:
                    await self._reap_artifacts_for(current)
                except Exception as exc:  # never let artifact reaping block pruning
                    logger.warning("artifact reap during prune failed: %s", exc)

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
        """Get Interactions for this conversation in chronological order.

        Batch-loads by ``conversation_id`` (one query, served by the
        ``conv_timestamp`` compound index) and orders in memory via
        ``_interaction_sort_key`` — the previous node-by-node chain walk cost
        N sequential DB fetches per call on the per-turn history hot path.

        Args:
            limit: Maximum number of interactions to return (0 for all).
                Forward order keeps the OLDEST ``limit``; ``reverse=True``
                keeps the NEWEST ``limit`` — matching the old walk semantics.
            reverse: If True, return in reverse chronological order (newest first)

        Returns:
            List of Interaction nodes in chronological order (oldest first by default)
        """
        from jvagent.memory.interaction import Interaction, interaction_sort_key

        found = await Interaction.find({"context.conversation_id": self.id})
        ordered = sorted(found or [], key=interaction_sort_key)
        if reverse:
            ordered.reverse()
        if limit > 0:
            ordered = ordered[:limit]
        return ordered

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

            # Add user utterance (if requested) - truncated if max_statement_length is set.
            # Skip when utterance is empty/whitespace so proactive interactions
            # (Agent.send_proactive_message) appear as a standalone assistant turn
            # rather than injecting a blank user role into the LLM history.
            if with_utterance and (interaction.utterance or "").strip():
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

    def get_tasks(
        self,
        status: Optional[Union[str, List[str]]] = None,
        owner_action: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get tasks, optionally filtered by status and/or owner_action.

        Args:
            status: Optional filter by a single status or a list of statuses.
            owner_action: Optional filter by action class name.

        Returns:
            List of task dicts.
        """
        from jvagent.memory.task_store import TaskStore

        store = TaskStore(self)
        return [
            t.to_dict() for t in store.list(status=status, owner_action=owner_action)
        ]

    def get_task(
        self,
        *,
        task_id: Optional[str] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        task_type: Optional[str] = None,
        owner_action: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get first task matching all provided filters.

        Args:
            task_id: Optional filter by task id.
            title: Optional filter by title.
            description: Optional filter by description.
            task_type: Optional filter by task type.
            owner_action: Optional filter by owner_action.
            status: Optional filter by status.

        Returns:
            First matching task dict, or None.
        """
        from jvagent.memory.task_store import TaskStore

        store = TaskStore(self)
        for t in store.list():
            if task_id is not None and t.id != task_id:
                continue
            if title is not None and t.title != title:
                continue
            if description is not None and t.description != description:
                continue
            if task_type is not None and t._task.task_type != task_type:
                continue
            if owner_action is not None and t._task.owner_action != owner_action:
                continue
            if status is not None and t.status != status:
                continue
            return t.to_dict()
        return None

    def get_active_tasks_for_context(self) -> List[str]:
        """Return list of task titles for context line.

        Returns:
            List of titles for active tasks.
        """
        return [t["title"] for t in self.get_tasks(status="active")]

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
            # Atomic decrement keeps deletion O(1); counters are reconciled in repair.
            ctx = await memory.get_context()
            await ctx.atomic_increment(memory.id, "total_conversations", -1)

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

        active_task_count = len(self.get_tasks(status="active"))

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
