"""Memory manager node for agent memory, user, and conversation management."""

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from jvspatial.core import Node
from jvspatial.core.annotations import attribute

if TYPE_CHECKING:
    from jvagent.memory.conversation import Conversation
    from jvagent.memory.user import User

logger = logging.getLogger(__name__)


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
        total_users: Cached count of outgoing User connections; reconciled with
            ``total_conversations`` via :meth:`refresh_memory_counters_from_graph`
        total_conversations: Cached sum of Conversation neighbors under edge-connected
            Users; reconciled via :meth:`refresh_memory_counters_from_graph`
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

        Uses a per-(memory, user_id) lock to prevent duplicate User creation
        under concurrent requests.

        Args:
            user_id: Unique identifier for the user
            create_if_missing: If True, create a new user if not found

        Returns:
            User node if found or created, None otherwise
        """
        from jvagent.memory.lock_manager import get_user_lock_manager

        lock_mgr = get_user_lock_manager()
        lock = await lock_mgr.acquire(f"{self.id}:{user_id}")
        async with lock:
            return await self._get_user_unlocked(user_id, create_if_missing)

    async def _get_user_unlocked(
        self, user_id: str, create_if_missing: bool
    ) -> Optional["User"]:
        from jvagent.core.app import App
        from jvagent.memory.user import User

        app = await App.get()
        now = await app.now() if app else datetime.now(timezone.utc)

        # AUDIT-memory HIGH-01/HIGH-02: scope by BOTH memory_id and user_id
        # on the graph traversal, not just user_id. Without the memory_id
        # filter, a connected User from a different Memory (legacy data,
        # cross-context contamination) would silently win.
        user = await self.node(node=User, memory_id=self.id, user_id=user_id)
        if user:
            user.last_seen = now
            await user.save()
            return user

        # Reconnect-on-create fallback: search the compound index. ALWAYS
        # scope by ``memory_id`` AND ``user_id`` together — the unique index
        # at user.py:16-24 covers this pair, but only when both fields are
        # part of the query. AUDIT-memory HIGH-01.
        scoped = await User.find_one(
            {"context.memory_id": self.id, "context.user_id": user_id}
        )
        if scoped:
            # Defensive double-check before connecting — memory_id MUST
            # match self; index should make this redundant, but lock-bypass
            # paths could in theory return a stale match.
            if getattr(scoped, "memory_id", None) != self.id:
                logger.warning(
                    "_get_user_unlocked: find_one returned User with "
                    "mismatched memory_id (got=%s expected=%s); ignoring",
                    getattr(scoped, "memory_id", None),
                    self.id,
                )
            else:
                if not await self.is_connected_to(scoped):
                    await self.connect(scoped)
                scoped.last_seen = now
                await scoped.save()
                return scoped

        if create_if_missing:
            user = await User.create(
                memory_id=self.id,
                user_id=user_id,
                created_at=now,
                last_seen=now,
            )
            await self.connect(user)
            ctx = await self.get_context()
            await ctx.atomic_increment(self.id, "total_users", 1)
            return user
        return None

    async def refresh_memory_counters_from_graph(self) -> Dict[str, int]:
        """Recount ``total_users`` and ``total_conversations`` from the live graph.

        Counters are maintained in hot paths via ``atomic_increment`` for O(1)
        performance. This method serves as a consistency check — it walks the
        full graph, corrects any drift, and logs discrepancies.

        The recount and the corrective save run under a Memory-scoped lock so
        a concurrent ``add_user`` / ``purge_user_memory`` cannot interleave
        and re-introduce drift mid-fix.

        Returns:
            Dict with ``drift_users`` and ``drift_conversations`` (0 = no drift).
        """
        from jvagent.memory.conversation import Conversation
        from jvagent.memory.lock_manager import get_user_lock_manager
        from jvagent.memory.user import User

        lock_mgr = get_user_lock_manager()
        lock = await lock_mgr.acquire(f"memory_counters:{self.id}")
        async with lock:
            target = await Memory.get(self.id)
            if target is None:
                return {"drift_users": 0, "drift_conversations": 0}
            users = await target.nodes(node=User)
            n_users = len(users)
            n_convs = 0
            for user in users:
                n_convs += await user.count_neighbors(node=Conversation)

            drift_users = n_users - (target.total_users or 0)
            drift_convs = n_convs - (target.total_conversations or 0)

            if drift_users != 0 or drift_convs != 0:
                logger.warning(
                    "Memory counter drift detected for %s: users=%+d convs=%+d",
                    self.id,
                    drift_users,
                    drift_convs,
                )
                target.total_users = n_users
                target.total_conversations = n_convs
                await target.save()

            self.total_users = n_users
            self.total_conversations = n_convs
            return {"drift_users": drift_users, "drift_conversations": drift_convs}

    async def users_scoped_to_this_memory(self) -> List["User"]:
        """Connected users that belong to this Memory root.

        AUDIT-memory MED-04: when ``JVAGENT_STRICT_USER_MEMORY_ID=true``
        (recommended for multi-tenant deployments), users with an empty
        ``memory_id`` are NOT included — they cannot be proven to belong
        to this Memory without an explicit owner.  Default behaviour
        preserves backward compatibility (include empty memory_id) so
        single-tenant tests and legacy graphs still see their users.
        """
        import os

        from jvagent.memory.user import User

        strict = (
            os.environ.get("JVAGENT_STRICT_USER_MEMORY_ID", "false").strip().lower()
            in {"true", "1", "yes", "on"}
        )

        connected = await self.nodes(node=User)
        result: List["User"] = []
        for u in connected:
            mid = getattr(u, "memory_id", "") or ""
            if mid == self.id:
                result.append(u)
                continue
            if not mid:
                if strict:
                    logger.warning(
                        "users_scoped_to_this_memory: skipping legacy user %s with "
                        "empty memory_id (JVAGENT_STRICT_USER_MEMORY_ID=true)",
                        getattr(u, "id", "unknown"),
                    )
                    continue
                result.append(u)
            # else: foreign memory_id — skip silently (was already excluded).
        return result

    async def get_users(self) -> List["User"]:
        """Get all Users under this Memory (edge + ``memory_id`` scope).

        Returns:
            List of User nodes
        """
        return await self.users_scoped_to_this_memory()

    async def _conversation_belongs_to_memory(
        self, conversation: "Conversation"
    ) -> bool:
        """True if the conversation's user is under this Memory root."""
        from jvagent.memory.user import User

        user = await conversation.node(direction="in", node=User)
        if not user:
            return False
        if user.memory_id and user.memory_id != self.id:
            return False
        return await self.is_connected_to(user)

    async def get_conversation_by_session(
        self, session_id: str
    ) -> Optional["Conversation"]:
        """Find Conversation by session_id scoped to this Memory's users.

        Uses ``Conversation.find_one({"context.session_id": ...})``; the Conversation
        model declares a compound index on ``context.session_id`` for scale.

        Args:
            session_id: Session identifier to search for

        Returns:
            Conversation node if found and owned by this memory, None otherwise
        """
        from jvagent.memory.conversation import Conversation

        conversation = await Conversation.find_one({"context.session_id": session_id})
        if not conversation:
            return None
        if await self._conversation_belongs_to_memory(conversation):
            return conversation
        return None

    async def _resolve_conversation_for_session_or_raise_foreign(
        self, session_id: str
    ) -> Optional["Conversation"]:
        """Return Conversation under this Memory, or None if no row exists.

        If a Conversation with this session_id exists but is not owned by this
        Memory, raises ValueError (session_id is globally unique).
        """
        from jvagent.memory.conversation import Conversation

        conversation = await Conversation.find_one({"context.session_id": session_id})
        if not conversation:
            return None
        if not await self._conversation_belongs_to_memory(conversation):
            raise ValueError(
                f"Session '{session_id}' is not accessible from this agent"
            )
        return conversation

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
    ) -> int:
        """Sync interaction_limit from agent and prune if over limit.

        Always syncs from agent when agent has a positive limit, so that changes
        to agent.yaml (increase or decrease) take effect on resume.

        Returns:
            Number of interactions removed by pruning (0 if none).
        """
        agent = await self.get_agent()
        if (
            not agent
            or not hasattr(agent, "interaction_limit")
            or agent.interaction_limit <= 0
        ):
            return 0
        agent_limit = agent.interaction_limit
        # Sync conversation limit from agent (handles both increase and decrease)
        if conversation.interaction_limit != agent_limit:
            conversation.interaction_limit = agent_limit
            await conversation.save()
        if conversation.interaction_count > conversation.interaction_limit:
            return await conversation._prune_old_interactions()
        return 0

    async def apply_interaction_limit_pruning_for_connected_users(self) -> int:
        """Sync limits and prune for every conversation under this Memory's users.

        Iterates users_scoped_to_this_memory(), then each User's Conversation nodes, and
        runs _ensure_conversation_interaction_limit on each.

        Returns:
            Total number of interactions removed across all conversations.
        """
        from jvagent.memory.conversation import Conversation
        from jvagent.memory.user import User

        total = 0
        for user in await self.users_scoped_to_this_memory():
            for conv in await user.nodes(node=Conversation):
                total += await self._ensure_conversation_interaction_limit(conv)
        return total

    async def get_user_by_session(self, session_id: str) -> Optional["User"]:
        """Find the User that owns a specific session.

        Args:
            session_id: Session identifier to search for

        Returns:
            User node if found, None otherwise
        """
        from jvagent.memory.user import User

        conversation = await self.get_conversation_by_session(session_id)
        if not conversation:
            return None
        return await conversation.node(direction="in", node=User)

    async def _check_is_new_user(self, user_id: str) -> bool:
        """Return True if no User with *user_id* is scoped to this Memory."""
        from jvagent.memory.user import User

        existing_user = await self.node(node=User, user_id=user_id)
        if (
            existing_user
            and existing_user.memory_id
            and existing_user.memory_id != self.id
        ):
            return True
        return existing_user is None

    async def _resolve_user(self, user_id: str, *, create: bool = True) -> "User":
        """Get or create a User, raising RuntimeError on failure."""
        from jvagent.memory.user import User

        user = await self.get_user(user_id, create_if_missing=create)
        if not user:
            raise RuntimeError(
                f"Failed to get/create user '{user_id}'"
                if create
                else f"User for session '{user_id}' not found"
            )
        return user

    @staticmethod
    def _should_set_name(user: "User", user_name: Optional[str], is_new: bool) -> bool:
        """True when *user_name* should be applied to *user*."""
        if not user_name:
            return False
        if is_new:
            return True
        return not user.name or user.name == "user"

    async def _maybe_set_user_name(
        self, user: "User", user_name: Optional[str], is_new: bool
    ) -> None:
        """Set *user_name* on *user* when conditions warrant it."""
        if self._should_set_name(user, user_name, is_new):
            await user.set_name(user_name)

    async def _create_anonymous_user_and_conversation(
        self, session_id: Optional[str], user_name: Optional[str], channel: str
    ) -> Tuple["User", "Conversation", str, str]:
        """Create anonymous User + Conversation; return (user, conv, user_id, session_id)."""
        new_user_id = f"user_{uuid.uuid4().hex[:16]}"
        user = await self._resolve_user(new_user_id)
        if user_name:
            await user.set_name(user_name)
        conversation = await user.create_conversation(
            session_id=session_id, channel=channel
        )
        return user, conversation, new_user_id, conversation.session_id

    async def _resume_or_create_conversation(
        self,
        user: "User",
        session_id: str,
        channel: str,
        is_new_user: bool,
        user_name: Optional[str],
    ) -> Tuple["Conversation", str, bool]:
        """Resume existing Conversation or create a new one under *user*.

        Returns (conversation, session_id, resumed) where *resumed* is True when
        the Conversation already existed.
        """
        conversation = await self._resolve_conversation_for_session_or_raise_foreign(
            session_id
        )
        if not conversation:
            await self._maybe_set_user_name(user, user_name, is_new_user)
            conversation = await user.create_conversation(
                session_id=session_id, channel=channel
            )
            return conversation, conversation.session_id, False
        return conversation, session_id, True

    async def get_session(
        self,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        user_name: Optional[str] = None,
        channel: str = "default",
    ) -> Tuple["User", "Conversation", str, str, bool]:
        """Resolve or create User and Conversation based on provided IDs.

        Interaction-limit pruning is NOT run on session resume paths so latency
        stays predictable as history grows. Limits are enforced when appending
        interactions and via
        :meth:`apply_interaction_limit_pruning_for_connected_users` for bulk
        maintenance.

        Handles four scenarios:
        1. No IDs → Create new User + Conversation (new_user=True)
        2. session_id only → Resume or create anonymous User + Conversation
        3. user_id only → Get/Create User, create new Conversation
        4. Both → Get/Create User; resume or create Conversation. Validates
           ownership; foreign session_id raises ValueError.

        Args:
            user_id: Optional user identifier
            session_id: Optional session identifier
            user_name: Optional display name for the user
            channel: Communication channel (default: "default")

        Returns:
            Tuple of (User, Conversation, resolved_user_id, resolved_session_id, new_user)

        Raises:
            RuntimeError: If user creation/lookup fails
            ValueError: If session is foreign to this Memory, or ownership validation fails
        """
        # Case 1: No IDs — create anonymous session
        if not user_id and not session_id:
            user, conv, uid, sid = await self._create_anonymous_user_and_conversation(
                None, user_name, channel
            )
            return user, conv, uid, sid, True

        # Case 2: session_id only — resume or create anonymous
        if session_id and not user_id:
            conversation = (
                await self._resolve_conversation_for_session_or_raise_foreign(
                    session_id
                )
            )
            if not conversation:
                user, conv, uid, sid = (
                    await self._create_anonymous_user_and_conversation(
                        session_id, user_name, channel
                    )
                )
                return user, conv, uid, sid, True

            user = await self._resolve_user(conversation.user_id, create=False)
            await self._maybe_set_user_name(user, user_name, is_new=False)
            return user, conversation, conversation.user_id, session_id, False

        # Cases 3 & 4 share user-resolution
        is_new = await self._check_is_new_user(user_id)  # type: ignore[arg-type]
        user = await self._resolve_user(user_id)  # type: ignore[arg-type]

        # Case 3: user_id only — new conversation
        if not session_id:
            await self._maybe_set_user_name(user, user_name, is_new)
            conversation = await user.create_conversation(channel=channel)
            return user, conversation, user_id, conversation.session_id, is_new  # type: ignore[arg-type]

        # Case 4: Both provided — resume or create, validate ownership
        conversation, resolved_sid, resumed = await self._resume_or_create_conversation(
            user, session_id, channel, is_new, user_name
        )
        if resumed:
            if conversation.user_id != user_id:
                raise ValueError(
                    f"Session '{session_id}' does not belong to user '{user_id}'"
                )
            await self._maybe_set_user_name(user, user_name, is_new=False)

        return user, conversation, user_id, resolved_sid, is_new  # type: ignore[arg-type]

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

        users_under = await self.users_scoped_to_this_memory()
        if user_id:
            users_under = [u for u in users_under if u.user_id == user_id]

        stats = {
            "total_users": (
                len(users_under) if user_id else await self.count_neighbors(node=User)
            ),
            "total_conversations": 0,
            "total_interactions": 0,
        }
        for u in users_under:
            stats["total_conversations"] += await u.count_neighbors(node=Conversation)
            for c in await u.nodes(node=Conversation):
                inters = await c.nodes(node=Interaction)
                stats["total_interactions"] += len(inters)
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
        from jvagent.memory.conversation import Conversation
        from jvagent.memory.user import User

        users = await self.users_scoped_to_this_memory()
        if user_id:
            users = [u for u in users if u.user_id == user_id]

        if not users:
            return None

        purged = []
        ctx = await self.get_context()
        for user in users:
            # Count conversations before cascade-delete so we can decrement accurately.
            n_convs = len(await user.nodes(node=Conversation))
            purged.append(user)
            await user.delete(cascade=True)
            await ctx.atomic_increment(self.id, "total_users", -1)
            if n_convs:
                await ctx.atomic_increment(self.id, "total_conversations", -n_convs)

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
        from jvagent.memory.user import User

        if conversation_id:
            conversation = await Conversation.get(conversation_id)
            if not conversation:
                return None
            # Ownership check: the conversation MUST belong to a User
            # connected to this Memory node. Without this, an admin could
            # delete conversations belonging to another Memory by supplying
            # any conversation_id. AUDIT-memory CRIT-03.
            owners = await conversation.nodes(node=User, direction="in")
            scoped_users = {
                u.id for u in await self.users_scoped_to_this_memory()
            }
            if not any(getattr(o, "id", None) in scoped_users for o in owners):
                logger.warning(
                    "purge_conversations: refused cross-memory purge",
                    extra={
                        "details": {
                            "conversation_id": conversation_id,
                            "memory_id": getattr(self, "id", None),
                            "owner_ids": [getattr(o, "id", None) for o in owners],
                        }
                    },
                )
                return None
            conversations_to_purge = [conversation]
        elif user_id:
            users = [
                u
                for u in await self.users_scoped_to_this_memory()
                if u.user_id == user_id
            ]
            if not users:
                return None
            conversations_to_purge = []
            for u in users:
                conversations_to_purge.extend(await u.nodes(node=Conversation))
            if not conversations_to_purge:
                return None
        else:
            # Graph-only: same external user_id can exist on multiple User nodes
            connected_users = await self.users_scoped_to_this_memory()
            conversations_to_purge = []
            for u in connected_users:
                conversations_to_purge.extend(await u.nodes(node=Conversation))
            if not conversations_to_purge:
                return None

        purged = []
        for conversation in conversations_to_purge:
            purged.append(conversation)
            await conversation.delete(cascade=True)

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
            Dict with validation, orphaned_interactions_deleted,
            orphaned_users_reconnected, dual_edges_removed,
            conversation_first_edges_restored, conversation_branch_edges_removed
        """
        validation = await self.validate_interaction_chain()
        if not validation["healthy"]:
            logger.warning(
                "Chain violations detected before repair: %s", validation["violations"]
            )

        deleted = await self._cleanup_orphaned_interactions(recent_minutes)
        dual_removed, first_restored, conv_branch_removed = (
            await self._repair_interaction_chain_invariants()
        )
        reconnected = await self._reconnect_orphaned_users()
        counters_fixed = await self._recalculate_counters()

        from jvagent.core.app import App

        app = await App.get()
        self.last_cleanup = await app.now() if app else datetime.now(timezone.utc)
        await self.save()

        return {
            "validation": validation,
            "orphaned_interactions_deleted": deleted,
            "orphaned_users_reconnected": reconnected,
            "dual_edges_removed": dual_removed,
            "conversation_first_edges_restored": first_restored,
            "conversation_branch_edges_removed": conv_branch_removed,
            "counters_fixed": counters_fixed,
        }

    async def validate_interaction_chain(self) -> Dict[str, Any]:
        """Read-only diagnostic: detect chain invariant violations without mutating state.

        Returns a structured report:
        {
            "conversations_checked": int,
            "violations": {
                "conversation_branches": [(conv_id, branch_count), ...],
                "missing_first_edges": [(conv_id, first_interaction_id), ...],
                "interaction_forks": [(interaction_id, fork_count), ...],
            },
            "healthy": bool,
        }
        """
        from jvagent.memory.conversation import Conversation
        from jvagent.memory.interaction import Interaction, interaction_sort_key

        users = await self.users_scoped_to_this_memory()
        conversations: list = []
        for user in users:
            conversations.extend(await user.nodes(node=Conversation))

        branches: list = []
        missing_first: list = []
        forks: list = []

        for conv in conversations:
            if conv.interaction_count <= 0:
                continue

            conv_out = await conv.nodes(node=Interaction, direction="out")
            if len(conv_out) > 1:
                branches.append((conv.id, len(conv_out)))

            first = await conv.get_first_interaction()
            if first and not await conv.is_connected_to(first):
                missing_first.append((conv.id, first.id))

            if not first:
                continue

            current = first
            seen = {first.id}
            while current:
                next_nodes = await current.nodes(node=Interaction, direction="out")
                if len(next_nodes) > 1:
                    forks.append((current.id, len(next_nodes)))
                    break
                if len(next_nodes) == 1:
                    current = next_nodes[0]
                    if current.id in seen:
                        break
                    seen.add(current.id)
                else:
                    break

        violations = {
            "conversation_branches": branches,
            "missing_first_edges": missing_first,
            "interaction_forks": forks,
        }
        healthy = not any(violations.values())

        return {
            "conversations_checked": len(conversations),
            "violations": violations,
            "healthy": healthy,
        }

    async def _repair_interaction_chain_invariants(self) -> Tuple[int, int, int]:
        """Repair dual edges, missing conversation->first edges, and conversation branches.

        Returns:
            Tuple of (dual_edges_removed, conversation_first_edges_restored,
            conversation_branch_edges_removed)
        """
        from jvagent.memory.conversation import Conversation
        from jvagent.memory.interaction import Interaction, interaction_sort_key

        dual_removed = 0
        first_restored = 0
        conv_branch_removed = 0

        users = await self.users_scoped_to_this_memory()
        conversations = []
        for user in users:
            conversations.extend(await user.nodes(node=Conversation))
        for conv in conversations:
            if conv.interaction_count <= 0:
                continue

            conv_out = await conv.nodes(node=Interaction, direction="out")
            if len(conv_out) > 1:
                conv_out.sort(key=interaction_sort_key)
                keep = conv_out[0]
                seen = {keep.id}
                tail = keep
                while True:
                    next_of_tail = await tail.nodes(node=Interaction, direction="out")
                    if len(next_of_tail) != 1:
                        break
                    cand = next_of_tail[0]
                    if cand.id in seen:
                        break
                    seen.add(cand.id)
                    tail = cand
                for extra in conv_out[1:]:
                    if await conv.is_connected_to(extra):
                        await tail.connect(extra, direction="both")
                        await conv.disconnect(extra)
                        conv_branch_removed += 1
                    tail = extra
                    while True:
                        next_of_tail = await tail.nodes(
                            node=Interaction, direction="out"
                        )
                        if len(next_of_tail) != 1:
                            break
                        cand = next_of_tail[0]
                        if cand.id in seen:
                            break
                        seen.add(cand.id)
                        tail = cand

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
                    next_nodes.sort(key=interaction_sort_key)
                    keep = next_nodes[0]
                    # Find tail of keep's chain (keep may have its own next nodes)
                    tail = keep
                    while True:
                        next_of_tail = await tail.nodes(
                            node=Interaction, direction="out"
                        )
                        if len(next_of_tail) != 1:
                            break
                        cand = next_of_tail[0]
                        if cand.id in seen:
                            break
                        seen.add(cand.id)
                        tail = cand
                    # Chain each extra to tail, then disconnect from current
                    for extra in next_nodes[1:]:
                        if await current.is_connected_to(extra):
                            await tail.connect(extra, direction="both")
                            await current.disconnect(extra)
                            dual_removed += 1
                        # Advance tail to end of extra's chain
                        tail = extra
                        while True:
                            next_of_tail = await tail.nodes(
                                node=Interaction, direction="out"
                            )
                            if len(next_of_tail) != 1:
                                break
                            cand = next_of_tail[0]
                            if cand.id in seen:
                                break
                            seen.add(cand.id)
                            tail = cand
                    current = keep
                elif len(next_nodes) == 1:
                    current = next_nodes[0]
                    if current.id in seen:
                        break
                    seen.add(current.id)
                else:
                    break

        return dual_removed, first_restored, conv_branch_removed

    async def _reconnect_orphaned_users(self) -> int:
        """Reconnect users with no incoming Memory edge to this Memory.

        Skips users already connected to any Memory so another agent's users
        are never stolen. Sets memory_id to this Memory before connecting.
        """
        from jvagent.memory.conversation import Conversation
        from jvagent.memory.user import User

        connected = await self.nodes(node=User)
        connected_ids = {u.id for u in connected}

        all_users = await User.find({"context.memory_id": self.id})
        context = await self.get_context()
        reconnected = 0
        for user in all_users:
            if user.id in connected_ids:
                continue
            mem_in = await user.nodes(direction="in", node=Memory)
            if mem_in:
                continue
            user.memory_id = self.id
            await user.save()
            await self.connect(user)
            reconnected += 1
        if reconnected:
            await self.refresh_memory_counters_from_graph()
        return reconnected

    async def _recalculate_counters(self) -> int:
        """Recalculate counters from the graph and correct any drift.

        Hot paths maintain counters via ``atomic_increment`` for O(1) performance.
        This method walks the full graph, re-derives ground truth, and fixes any
        drift from concurrent mutations or orphan cleanup.

        Returns:
            Number of counters that were corrected.
        """
        from jvagent.memory.conversation import Conversation
        from jvagent.memory.user import User

        fixed = 0
        users = await self.nodes(node=User)
        actual_users = await self.count_neighbors(node=User)
        if self.total_users != actual_users:
            logger.debug(
                "Memory %s: total_users drift %d -> %d",
                self.id,
                self.total_users,
                actual_users,
            )
            self.total_users = actual_users
            fixed += 1

        actual_conversations = 0
        all_convs: list = []
        for user in users:
            convs = await user.nodes(node=Conversation)
            actual_conversations += await user.count_neighbors(node=Conversation)
            all_convs.extend(convs)
        if self.total_conversations != actual_conversations:
            logger.debug(
                "Memory %s: total_conversations drift %d -> %d",
                self.id,
                self.total_conversations,
                actual_conversations,
            )
            self.total_conversations = actual_conversations
            fixed += 1

        if fixed:
            await self.save()

        # Reconcile interaction_count on each conversation using a count query
        # (avoids loading every Interaction object just to count them).
        from jvspatial.core import get_default_context

        db = get_default_context().database
        for conv in all_convs:
            actual_count = await db.count(
                "node",
                {"entity": "Interaction", "context.conversation_id": conv.id},
            )
            if conv.interaction_count != actual_count:
                conv.interaction_count = actual_count
                # Repair last_interaction_id only if the count drifted.
                if actual_count > 0:
                    interactions = await conv.get_interactions(limit=0)
                    conv.last_interaction_id = (
                        interactions[-1].id if interactions else None
                    )
                else:
                    conv.last_interaction_id = None
                await conv.save()
                fixed += 1

        return fixed

    async def _cleanup_orphaned_interactions(
        self, recent_minutes: Optional[int] = None
    ) -> int:
        """Delete orphaned interactions whose conversation node no longer exists.

        Uses id-range pagination instead of a ``$nin`` query to avoid building
        an unbounded exclusion list that Mongo cannot index efficiently.  For
        each page of Interaction nodes we collect the distinct ``conversation_id``
        values and verify them with a single ``$in`` lookup.

        Args:
            recent_minutes: If set, only delete orphans with started_at within
                this many minutes (for fast cold-start cleanup). None = all orphans.

        Returns:
            Number of orphaned interactions deleted
        """
        from datetime import datetime, timedelta, timezone

        from jvspatial.core import get_default_context

        from jvagent.core.app import App
        from jvagent.memory.conversation import Conversation
        from jvagent.memory.interaction import Interaction

        app = await App.get()
        now = await app.now() if app else datetime.now(timezone.utc)
        context = get_default_context()
        db = context.database
        BATCH = 500

        # Build a time filter for the paged scan when recent_minutes is set.
        time_filter: Dict[str, Any] = {}
        if recent_minutes is not None and recent_minutes > 0:
            cutoff = now - timedelta(minutes=recent_minutes)
            time_filter["context.started_at"] = {"$gte": cutoff}

        # Scope to this Memory and legacy/unscoped rows (missing or empty memory_id).
        base_filter: Dict[str, Any] = {
            "entity": "Interaction",
            "context.conversation_id": {"$ne": ""},
        }
        if self.id:
            base_filter["$or"] = [
                {"context.memory_id": self.id},
                {"context.memory_id": ""},
                {"context.memory_id": None},
            ]
        base_filter.update(time_filter)

        deleted = 0
        last_id: Optional[str] = None

        while True:
            page_filter = dict(base_filter)
            if last_id:
                page_filter["id"] = {"$gt": last_id}
            rows = await db.find(
                "node",
                page_filter,
                limit=BATCH,
                sort=[("id", 1)],
            )
            if not rows:
                break

            # Collect distinct conversation_ids referenced in this page
            page_conv_ids = {
                r.get("context", {}).get("conversation_id") or r.get("conversation_id")
                for r in rows
            } - {None, ""}

            # Batch-verify which conversations actually exist
            existing_conv_ids: set = set()
            if page_conv_ids:
                existing_rows = await db.find(
                    "node",
                    {"id": {"$in": list(page_conv_ids)}, "entity": "Conversation"},
                    limit=len(page_conv_ids) + 1,
                )
                existing_conv_ids = {r["id"] for r in existing_rows if r.get("id")}

            for row in rows:
                ctx = row.get("context", {})
                conv_id = ctx.get("conversation_id") or row.get("conversation_id")
                if not conv_id:
                    continue
                if conv_id in existing_conv_ids:
                    continue
                # conversation is truly gone – delete the interaction
                interaction_id = row.get("id")
                if not interaction_id:
                    continue
                try:
                    interaction = await Interaction.get(interaction_id)
                    if interaction:
                        await interaction.delete(cascade=True)
                        deleted += 1
                except Exception as exc:
                    # Surface the failure: silently swallowing this loses both
                    # the cleanup AND the counter-correction signal that the
                    # repair engine relies on. Log and continue so one bad row
                    # does not abort the rest of the page.
                    logger.warning(
                        "_cleanup_orphaned_interactions: delete %s failed (%s)",
                        interaction_id,
                        exc,
                    )

            last_id = rows[-1].get("id")
            if len(rows) < BATCH:
                break

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

        users = await self.users_scoped_to_this_memory()
        if user_id:
            users = [u for u in users if u.user_id == user_id]

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
