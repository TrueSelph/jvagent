"""Memory manager node for agent memory, user, and conversation management."""

import asyncio
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from jvspatial.core import Node
from jvspatial.core.annotations import attribute

if TYPE_CHECKING:
    from jvagent.memory.conversation import Conversation
    from jvagent.memory.user import User


class Memory(Node):
    """Memory manager - root node for User/Conversation/Interaction graph.

    The Memory node manages all memory-related entities for an agent including:
    - User nodes (representing end-users interacting with the agent)
    - Conversation nodes (representing conversation sessions)
    - Collection nodes (representing knowledge collections)

    Entity Relationships (Edge-Connected Nodes):
        Memory (Node)
            └── [edge] ──► User (Node)
                              └── [edge] ──► Conversation (Node)
                                                  └── [edge] ──► Interaction (Node)

    Cascade Delete Behavior:
        - Delete User → Cascades to all Conversations → Cascades to all Interactions
        - Delete Conversation → Cascades to all Interactions
        - Delete Memory → Cascades to all Users → All Conversations → All Interactions

    Attributes:
        total_users: Total number of users
        total_conversations: Total number of conversations
        total_collections: Total number of collections
        last_cleanup: Timestamp of last cleanup operation
    """

    # Counters
    total_users: int = attribute(default=0, description="Total number of users")
    total_conversations: int = attribute(
        default=0, description="Total number of conversations"
    )
    total_collections: int = attribute(
        default=0, description="Total number of collections"
    )

    # Maintenance
    last_cleanup: Optional[datetime] = attribute(
        default=None, description="Timestamp of last cleanup operation"
    )

    async def get_user(
        self, user_id: str, create_if_missing: bool = True
    ) -> Optional["User"]:
        """Get or create User by user_id.

        Args:
            user_id: Unique identifier for the user
            create_if_missing: If True, create a new user if not found

        Returns:
            User node if found or created, None otherwise
        """
        from jvagent.core.app import App
        from jvagent.memory.user import User

        app = await App.get()
        now = await app.now() if app else datetime.now(timezone.utc)

        # Use node() to get a single connected user (no need to dereference list)
        user = await self.node(node=User, user_id=user_id)
        if user:
            # Update last seen
            user.last_seen = now
            await user.save()
            return user

        # User not connected to this Memory node - check if exists globally
        # This handles orphaned users that exist but lost their edge connection
        existing_user = await User.find_one({"context.user_id": user_id})
        if existing_user:
            # Reconnect the orphaned user to this Memory node
            await self.connect(existing_user)
            existing_user.last_seen = now
            await existing_user.save()
            return existing_user

        if create_if_missing:
            user = await User.create(user_id=user_id, created_at=now, last_seen=now)
            await self.connect(user)  # Creates edge: Memory --> User
            self.total_users += 1
            await self.save()
            return user
        return None

    async def get_users(self) -> List["User"]:
        """Get all connected Users.

        Returns:
            List of User nodes
        """
        from jvagent.memory.user import User

        return await self.nodes(node=User)

    async def get_conversation_by_session(
        self, session_id: str
    ) -> Optional["Conversation"]:
        """Find Conversation by session_id across all Users.

        Args:
            session_id: Session identifier to search for

        Returns:
            Conversation node if found, None otherwise
        """
        from jvagent.memory.conversation import Conversation

        # Use find_one for optimal performance
        return await Conversation.find_one({"context.session_id": session_id})

    async def get_agent(self) -> Optional[Any]:
        """Get the Agent node this Memory belongs to.

        Memory is connected to Agent via bidirectional edge.
        Agent connects to Memory, so from Memory's perspective Agent is incoming.

        Returns:
            Agent instance if found, None otherwise
        """
        from jvagent.core.agent import Agent

        return await self.node(direction="in", node=Agent)

    async def _ensure_conversation_interaction_limit(
        self, conversation: "Conversation"
    ) -> None:
        """Sync interaction_limit from agent and prune if over limit.

        Always syncs from agent when agent has a positive limit, so that changes
        to agent.yaml (increase or decrease) take effect on resume.
        """
        agent = await self.get_agent()
        if (
            not agent
            or not hasattr(agent, "interaction_limit")
            or agent.interaction_limit <= 0
        ):
            return
        agent_limit = agent.interaction_limit
        # Sync conversation limit from agent (handles both increase and decrease)
        if conversation.interaction_limit != agent_limit:
            conversation.interaction_limit = agent_limit
            await conversation.save()
        if conversation.interaction_count > conversation.interaction_limit:
            await conversation._prune_old_interactions()

    async def get_user_by_session(self, session_id: str) -> Optional["User"]:
        """Find the User that owns a specific session.

        Args:
            session_id: Session identifier to search for

        Returns:
            User node if found, None otherwise
        """
        from jvagent.memory.conversation import Conversation
        from jvagent.memory.user import User

        # Use find_one for optimal performance
        conversation = await Conversation.find_one({"context.session_id": session_id})
        if not conversation:
            return None

        # Get user by user_id from conversation
        return await User.find_one({"context.user_id": conversation.user_id})

    async def get_session(
        self,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        user_name: Optional[str] = None,
        channel: str = "default",
    ) -> Tuple["User", "Conversation", str, str, bool]:
        """Resolve or create User and Conversation based on provided IDs.

        Handles four scenarios for user/session resolution:
        1. No user_id, no session_id → Create new User + Conversation (new_user=True)
        2. session_id only → Lookup existing Conversation, get associated User (new_user=False)
        3. user_id only → Get/Create User, create new Conversation (new_user=True if User was created)
        4. Both provided → Validate session belongs to user, return both (new_user=False)

        First-time users are determined by whether a User node is newly created,
        regardless of whether a user_id is provided.

        Args:
            user_id: Optional user identifier
            session_id: Optional session identifier
            channel: Communication channel (e.g., 'default', 'whatsapp', 'email')

        Returns:
            Tuple of (User, Conversation, resolved_user_id, resolved_session_id, new_user)
            where new_user is True if a new User node was created, False otherwise

        Raises:
            RuntimeError: If user creation/lookup fails
            ValueError: If session not found or validation fails
        """
        from jvagent.memory.conversation import Conversation
        from jvagent.memory.user import User

        # Case 1: No IDs - create new user and conversation
        if not user_id and not session_id:
            new_user_id = f"user_{uuid.uuid4().hex[:16]}"
            user = await self.get_user(new_user_id, create_if_missing=True)
            if not user:
                raise RuntimeError("Failed to create user")

            # Set name if provided
            if user_name:
                await user.set_name(user_name)

            conversation = await user.create_conversation(channel=channel)
            return user, conversation, new_user_id, conversation.session_id, True

        # Case 2: session_id only - lookup conversation
        if session_id and not user_id:
            conversation = await self.get_conversation_by_session(session_id)
            if not conversation:
                raise ValueError(f"Session '{session_id}' not found")
            user = await self.get_user(conversation.user_id, create_if_missing=False)
            if not user:
                raise RuntimeError(f"User for session '{session_id}' not found")

            # Update name if provided and not set
            if user_name and (not user.name or user.name == "user"):
                await user.set_name(user_name)

            await self._ensure_conversation_interaction_limit(conversation)
            return user, conversation, conversation.user_id, session_id, False

        # Case 3: user_id only - get/create user, create conversation
        # Check if user exists to determine if it's a new user
        if user_id and not session_id:
            # Check if user already exists before creating
            existing_user = await self.node(node=User, user_id=user_id)
            is_new_user = existing_user is None

            user = await self.get_user(user_id, create_if_missing=True)
            if not user:
                raise RuntimeError(f"Failed to get/create user '{user_id}'")

            # Update name if provided (especially if new user)
            if user_name and (is_new_user or not user.name or user.name == "user"):
                await user.set_name(user_name)

            conversation = await user.create_conversation(channel=channel)
            return user, conversation, user_id, conversation.session_id, is_new_user

        # Case 4: Both provided - validate and use
        # Parallelize conversation and user lookups since they're independent
        if user_id and session_id:
            conversation_task = self.get_conversation_by_session(session_id)
            user_task = self.get_user(user_id, create_if_missing=False)
            conversation, user = await asyncio.gather(conversation_task, user_task)

            if not conversation:
                raise ValueError(f"Session '{session_id}' not found")
            if not user:
                raise RuntimeError(f"User '{user_id}' not found")
            if conversation.user_id != user_id:
                raise ValueError(
                    f"Session '{session_id}' does not belong to user '{user_id}'"
                )

            # Update name if provided and not set
            if user_name and (not user.name or user.name == "user"):
                await user.set_name(user_name)

            await self._ensure_conversation_interaction_limit(conversation)
            return user, conversation, user_id, session_id, False

        raise ValueError("Invalid user_id/session_id combination")

    async def memory_healthcheck(self, user_id: str = "") -> Dict[str, int]:
        """Get memory health statistics.

        Args:
            user_id: Optional user_id to filter stats for

        Returns:
            Dictionary with memory statistics
        """
        from jvagent.memory.conversation import Conversation
        from jvagent.memory.interaction import Interaction
        from jvagent.memory.user import User

        # Use count() for efficient database-level counting without loading records
        user_query = {}
        if user_id:
            user_query = {"context.user_id": user_id}

        stats = {
            "total_users": await User.count(user_query),
            "total_conversations": 0,
            "total_interactions": 0,
        }

        # Count conversations using count() for optimal performance
        conv_query = {}
        if user_id:
            conv_query = {"context.user_id": user_id}
        stats["total_conversations"] = await Conversation.count(conv_query)

        # Count interactions using count() for optimal performance
        interaction_query = {}
        if user_id:
            interaction_query = {"context.user_id": user_id}
        stats["total_interactions"] = await Interaction.count(interaction_query)

        return stats

    async def purge_user_memory(
        self, user_id: Optional[str] = None
    ) -> Optional[List["User"]]:
        """Purge user memory (cascade deletes conversations and interactions).

        Args:
            user_id: Optional specific user to purge. If None, purges all users.

        Returns:
            List of purged users, or None if no users found
        """
        from jvagent.memory.user import User

        # Use nodes() with filters to leverage graph structure and database-level filtering
        if user_id:
            users = await self.nodes(node=User, user_id=user_id)
        else:
            users = await self.nodes(node=User)

        if not users:
            return None

        purged = []
        for user in users:
            purged.append(user)
            # Cascade delete will call Conversation.delete() for each conversation,
            # which will properly decrement total_conversations counter
            await user.delete(cascade=True)
            self.total_users = max(0, self.total_users - 1)

        await self.save()
        return purged

    async def purge_conversation(
        self, conversation_id: Optional[str] = None
    ) -> Optional[List["Conversation"]]:
        """Purge conversation(s) (cascade deletes interactions).

        Args:
            conversation_id: Optional specific conversation ID to purge. If None, purges all conversations.

        Returns:
            List of purged conversations, or None if no conversations found
        """
        return await self.purge_conversations(
            user_id=None, conversation_id=conversation_id
        )

    async def purge_conversations(
        self,
        user_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> Optional[List["Conversation"]]:
        """Purge conversations (cascade deletes interactions).

        Uses both graph and database queries to handle broken graphs.
        Does not run repair; orphans remain until admin calls repair endpoint.

        Args:
            user_id: Optional - purge only this user's conversations. If None and
                conversation_id is None, purges all users.
            conversation_id: Optional - purge only this conversation. If set,
                user_id is ignored for the purge scope.

        Returns:
            List of purged conversations, or None if no conversations found
        """
        from jvagent.memory.conversation import Conversation

        if conversation_id:
            conversation = await Conversation.get(conversation_id)
            if not conversation:
                return None
            conversations_to_purge = [conversation]
        elif user_id:
            conversations_to_purge = await Conversation.find(
                {"context.user_id": user_id}
            )
            if not conversations_to_purge:
                return None
        else:
            conversations_to_purge = await Conversation.find()
            if not conversations_to_purge:
                return None

        purged = []
        for conversation in conversations_to_purge:
            purged.append(conversation)
            await conversation.delete(cascade=True)

        await self.save()

        return purged

    async def repair_memory(
        self, recent_minutes: Optional[int] = None
    ) -> Dict[str, Any]:
        """Run all orphan cleanup and memory repair procedures.

        Manually triggered only via the repair endpoint. No automatic triggers.

        Args:
            recent_minutes: If set, only clean orphan interactions from the last
                N minutes. None = all orphans.

        Returns:
            Dict with orphaned_interactions_deleted, orphaned_users_reconnected,
            dual_edges_removed, conversation_first_edges_restored
        """
        deleted = await self._cleanup_orphaned_interactions(recent_minutes)
        dual_removed, first_restored = await self._repair_interaction_chain_invariants()
        reconnected = await self._reconnect_orphaned_users()

        from jvagent.core.app import App

        app = await App.get()
        self.last_cleanup = await app.now() if app else datetime.now(timezone.utc)
        await self.save()

        return {
            "orphaned_interactions_deleted": deleted,
            "orphaned_users_reconnected": reconnected,
            "dual_edges_removed": dual_removed,
            "conversation_first_edges_restored": first_restored,
        }

    async def _repair_interaction_chain_invariants(self) -> Tuple[int, int]:
        """Repair dual edges and missing conversation->first edges.

        Returns:
            Tuple of (dual_edges_removed, conversation_first_edges_restored)
        """
        from jvagent.memory.conversation import Conversation
        from jvagent.memory.interaction import Interaction

        dual_removed = 0
        first_restored = 0

        conversations = await Conversation.find()
        for conv in conversations:
            if conv.interaction_count <= 0:
                continue

            first = await conv.get_first_interaction()
            if not first:
                continue

            if not await conv.is_connected_to(first):
                await conv.connect(first, direction="out")
                first_restored += 1

            current = first
            seen = {first.id}
            while current:
                next_nodes = await current.nodes(node=Interaction, direction="out")
                if len(next_nodes) > 1:
                    next_nodes.sort(key=lambda n: (n.started_at, n.id))
                    keep = next_nodes[0]
                    for extra in next_nodes[1:]:
                        if await current.is_connected_to(extra):
                            await current.disconnect(extra)
                            dual_removed += 1
                    current = keep
                elif len(next_nodes) == 1:
                    current = next_nodes[0]
                    if current.id in seen:
                        break
                    seen.add(current.id)
                else:
                    break

        return dual_removed, first_restored

    async def _reconnect_orphaned_users(self) -> int:
        """Find users in DB with no edge to this Memory and reconnect them."""
        from jvagent.memory.user import User

        connected = await self.nodes(node=User)
        connected_ids = {u.id for u in connected}

        all_users = await User.find()
        reconnected = 0
        for user in all_users:
            if user.id not in connected_ids:
                await self.connect(user)
                reconnected += 1
        return reconnected

    async def _cleanup_orphaned_interactions(
        self, recent_minutes: Optional[int] = None
    ) -> int:
        """Delete orphaned interactions (no graph edge to a valid conversation).

        Internal helper for repair_memory. When recent_minutes is set, only
        cleans orphans from the last N minutes.

        Args:
            recent_minutes: If set, only delete orphans with started_at within
                this many minutes (for fast cold-start cleanup). None = all orphans.

        Returns:
            Number of orphaned interactions deleted
        """
        from datetime import datetime, timedelta, timezone

        from jvagent.memory.conversation import Conversation
        from jvagent.memory.interaction import Interaction

        remaining_conversations = await Conversation.find()
        valid_conv_ids = list({c.id for c in remaining_conversations}) + [""]

        from jvagent.core.app import App

        app = await App.get()
        now = await app.now() if app else datetime.now(timezone.utc)

        query: Dict[str, Any] = {"context.conversation_id": {"$nin": valid_conv_ids}}
        if recent_minutes is not None and recent_minutes > 0:
            cutoff = now - timedelta(minutes=recent_minutes)
            query["context.started_at"] = {"$gte": cutoff}

        orphaned = await Interaction.find(query)
        deleted = 0
        for interaction in orphaned:
            if interaction.conversation_id:
                try:
                    await interaction.delete(cascade=True)
                    deleted += 1
                except Exception:
                    pass
        return deleted

    async def export_memory(self, user_id: str = "") -> Dict[str, Any]:
        """Export memory state for backup/migration.

        Args:
            user_id: Optional user_id to export. If empty, exports all.

        Returns:
            Dictionary with exported memory data
        """
        from jvagent.memory.conversation import Conversation
        from jvagent.memory.interaction import Interaction
        from jvagent.memory.user import User

        # Use nodes() with filters to leverage graph structure and database-level filtering
        if user_id:
            users = await self.nodes(node=User, user_id=user_id)
        else:
            users = await self.nodes(node=User)

        export_data: Dict[str, Any] = {"users": []}

        for user in users:
            user_data = await user.export()
            user_data["conversations"] = []

            # Use nodes() to get connected conversations (leverages graph structure)
            conversations = await user.nodes(node=Conversation)
            for conv in conversations:
                conv_data = await conv.export()
                conv_data["interactions"] = []

                # Use nodes() to get connected interactions (leverages graph structure)
                interactions = await conv.nodes(node=Interaction)
                for interaction in interactions:
                    interaction_data = await interaction.export()
                    conv_data["interactions"].append(interaction_data)

                user_data["conversations"].append(conv_data)

            export_data["users"].append(user_data)

        return export_data
