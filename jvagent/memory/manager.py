"""Memory manager node for agent memory, user, and conversation management."""

import uuid
from datetime import datetime
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
        from jvagent.memory.user import User

        # Search connected Users
        users: List[User] = await self.nodes(node=User)
        for user in users:
            if user.user_id == user_id:
                # Update last seen
                user.last_seen = datetime.utcnow()
                await user.save()
                return user

        if create_if_missing:
            user = await User.create(user_id=user_id)
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
        from jvagent.memory.user import User

        users: List[User] = await self.nodes(node=User)
        for user in users:
            conv = await user.get_conversation_by_session(session_id)
            if conv:
                return conv
        return None

    async def get_user_by_session(self, session_id: str) -> Optional["User"]:
        """Find the User that owns a specific session.

        Args:
            session_id: Session identifier to search for

        Returns:
            User node if found, None otherwise
        """
        from jvagent.memory.user import User

        users: List[User] = await self.nodes(node=User)
        for user in users:
            conv = await user.get_conversation_by_session(session_id)
            if conv:
                return user
        return None

    async def get_session(
        self,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        channel: str = "default",
    ) -> Tuple["User", "Conversation", str, str]:
        """Resolve or create User and Conversation based on provided IDs.

        Handles four scenarios for user/session resolution:
        1. No user_id, no session_id → Create new User + Conversation
        2. session_id only → Lookup existing Conversation, get associated User
        3. user_id only → Get/Create User, create new Conversation
        4. Both provided → Validate session belongs to user, return both

        Args:
            user_id: Optional user identifier
            session_id: Optional session identifier
            channel: Communication channel (e.g., 'default', 'whatsapp', 'email')

        Returns:
            Tuple of (User, Conversation, resolved_user_id, resolved_session_id)

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
            conversation = await user.create_conversation(channel=channel)
            return user, conversation, new_user_id, conversation.session_id

        # Case 2: session_id only - lookup conversation
        if session_id and not user_id:
            conversation = await self.get_conversation_by_session(session_id)
            if not conversation:
                raise ValueError(f"Session '{session_id}' not found")
            user = await self.get_user(
                conversation.user_id, create_if_missing=False
            )
            if not user:
                raise RuntimeError(f"User for session '{session_id}' not found")
            return user, conversation, conversation.user_id, session_id

        # Case 3: user_id only - get/create user, create conversation
        if user_id and not session_id:
            user = await self.get_user(user_id, create_if_missing=True)
            if not user:
                raise RuntimeError(f"Failed to get/create user '{user_id}'")
            conversation = await user.create_conversation(channel=channel)
            return user, conversation, user_id, conversation.session_id

        # Case 4: Both provided - validate and use
        if user_id and session_id:
            conversation = await self.get_conversation_by_session(session_id)
            if not conversation:
                raise ValueError(f"Session '{session_id}' not found")
            if conversation.user_id != user_id:
                raise ValueError(
                    f"Session '{session_id}' does not belong to user '{user_id}'"
                )
            user = await self.get_user(user_id, create_if_missing=False)
            if not user:
                raise RuntimeError(f"User '{user_id}' not found")
            return user, conversation, user_id, session_id

        raise ValueError("Invalid user_id/session_id combination")

    async def memory_healthcheck(self, user_id: str = "") -> Dict[str, int]:
        """Get memory health statistics.

        Args:
            user_id: Optional user_id to filter stats for

        Returns:
            Dictionary with memory statistics
        """
        from jvagent.memory.user import User

        stats = {
            "total_users": 0,
            "total_conversations": 0,
            "total_interactions": 0,
        }

        users: List[User] = await self.nodes(node=User)
        if user_id:
            users = [u for u in users if u.user_id == user_id]

        stats["total_users"] = len(users)

        for user in users:
            conversations = await user.list_conversations()
            stats["total_conversations"] += len(conversations)
            for conv in conversations:
                interactions = await conv.get_interactions(limit=0)
                stats["total_interactions"] += len(interactions)

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

        users: List[User] = await self.nodes(node=User)
        if user_id:
            users = [u for u in users if u.user_id == user_id]

        if not users:
            return None

        purged = []
        for user in users:
            purged.append(user)
            await user.delete(cascade=True)
            self.total_users = max(0, self.total_users - 1)

        await self.save()
        return purged

    async def export_memory(self, user_id: str = "") -> Dict[str, Any]:
        """Export memory state for backup/migration.

        Args:
            user_id: Optional user_id to export. If empty, exports all.

        Returns:
            Dictionary with exported memory data
        """
        from jvagent.memory.user import User

        users: List[User] = await self.nodes(node=User)
        if user_id:
            users = [u for u in users if u.user_id == user_id]

        export_data: Dict[str, Any] = {"users": []}

        for user in users:
            user_data = await user.export()
            user_data["conversations"] = []

            conversations = await user.list_conversations()
            for conv in conversations:
                conv_data = await conv.export()
                conv_data["interactions"] = []

                interactions = await conv.get_interactions(limit=0)
                for interaction in interactions:
                    interaction_data = await interaction.export()
                    conv_data["interactions"].append(interaction_data)

                user_data["conversations"].append(conv_data)

            export_data["users"].append(user_data)

        return export_data
