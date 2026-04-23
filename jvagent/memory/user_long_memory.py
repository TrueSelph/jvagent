"""UserLongMemory node for graph-based long-term memory storage.

Long-term memory is stored as a set of category nodes connected to the User node.
Each category (e.g., Interests, Facts & Preferences) is a separate UserLongMemoryNode,
allowing targeted read and write access per category rather than a single flat blob.

Graph structure:
    User
      └──> UserLongMemory
             └──> UserLongMemoryNode (category="interests")
             └──> UserLongMemoryNode (category="facts_and_preferences")
             └──> UserLongMemoryNode (category="open_threads")
             └──> UserLongMemoryNode (category="recent_events")
             └──> UserLongMemoryNode (category="<any custom category>")
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.core import Node
from jvspatial.core.annotations import attribute, compound_index

if TYPE_CHECKING:
    from jvagent.memory.user import User

# Default categories created on first write if they don't exist yet
DEFAULT_CATEGORIES: List[str] = [
    "interests",
    "facts_and_preferences",
    "open_threads",
    "recent_events",
]

# Human-readable titles for each category (used for prompts and display)
CATEGORY_TITLES: Dict[str, str] = {
    "interests": "Interests",
    "facts_and_preferences": "Facts & Preferences",
    "open_threads": "Unresolved Open Threads",
    "recent_events": "Recent Events & Context",
}


@compound_index(
    [("user_node_id", 1), ("category", 1)],
    name="user_node_category",
    unique=True,
    partial_filter_expression={
        "context.user_node_id": {"$gt": ""},
        "context.category": {"$gt": ""},
    },
)
class UserLongMemoryNode(Node):
    """A single long-memory category node attached to a User.

    Each node holds the markdown content for one memory category.
    Multiple nodes are attached to a User via outgoing edges, one per category.
    The category key acts as a stable identifier (e.g., "interests").

    Attributes:
        user_node_id: Graph id of the owning User node (unique per agent memory)
        user_id:      Owner's external user_id (matches User.user_id; denormalized)
        category:     Stable key for this category (e.g., "interests")
        title:        Human-readable title (e.g., "Interests")
        content:      Markdown-formatted memory content for this category
        updated_at:   Timestamp of last content update
        created_at:   Timestamp of node creation
    """

    user_node_id: str = attribute(
        indexed=True,
        default="",
        description="Graph id of the owning User node",
    )
    user_id: str = attribute(
        indexed=True,
        default="",
        description="Owner's user_id — matches User.user_id (denormalized)",
    )
    category: str = attribute(
        indexed=True,
        default="",
        description="Stable category key (e.g., 'interests', 'open_threads')",
    )
    title: str = attribute(
        default="",
        description="Human-readable category title for display and prompts",
    )
    content: str = attribute(
        default="",
        description="Markdown-formatted memory content for this category",
    )
    updated_at: Optional[datetime] = attribute(
        default=None,
        description="Timestamp of last content update",
    )
    created_at: datetime = attribute(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp of node creation",
    )
    needs_indexing: bool = attribute(
        default=False,
        description="Flag denoting if the content is recently updated and needs PageIndex assimilation",
    )
    keywords: List[str] = attribute(
        default_factory=list,
        description="List of key terms or topics extracted from the content for quick indexing",
    )

    def is_empty(self) -> bool:
        """Return True if this category node has no content yet."""
        return not self.content or not self.content.strip()

    async def update_content(self, new_content: str) -> bool:
        """Update content and timestamp, save if changed.

        Args:
            new_content: New markdown content for this category.

        Returns:
            True if content was changed and saved, False if unchanged.
        """
        new_content = new_content.strip()
        if new_content == (self.content or "").strip():
            return False
        self.content = new_content
        self.updated_at = datetime.now(timezone.utc)
        self.needs_indexing = True
        await self.save()
        return True


class UserLongMemory(Node):
    """Lightweight anchor node for a user's long-term memory graph.

    Attached to the User node via an outgoing edge. Serves as the root of
    the long-memory sub-graph: each memory category is a UserLongMemoryNode
    connected outward from this node.

    Graph structure:
        User ──> UserLongMemory ──> UserLongMemoryNode (interests)
                             ──> UserLongMemoryNode (facts_and_preferences)
                             ──> UserLongMemoryNode (open_threads)
                             ──> UserLongMemoryNode (recent_events)
                             ──> UserLongMemoryNode (<custom>)

    Attributes:
        user_node_id: Graph id of the owning User node (unique anchor per User)
        user_id:      Owner's external user_id (matches User.user_id)
        created_at: Timestamp of creation
    """

    user_node_id: str = attribute(
        indexed=True,
        index_unique=True,
        index_partial_filter_expression={"context.user_node_id": {"$gt": ""}},
        default="",
        description="Graph id of the owning User node",
    )
    user_id: str = attribute(
        indexed=True,
        default="",
        description="Owner's user_id — matches User.user_id",
    )
    created_at: datetime = attribute(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp of UserLongMemory node creation",
    )
    pageindex_markdown_sha256: Optional[str] = attribute(
        default=None,
        description="SHA-256 hex digest of markdown last assimilated to PageIndex; skip re-ingest when unchanged",
    )

    # -------------------------------------------------------------------------
    # Category node access
    # -------------------------------------------------------------------------

    async def get_category(self, category: str) -> Optional[UserLongMemoryNode]:
        """Get the UserLongMemoryNode for a specific category.

        Uses indexed lookup on (user_node_id, category) when set; otherwise
        falls back to (user_id, category) for pre-migration data.

        Args:
            category: Stable category key (e.g., "interests").

        Returns:
            UserLongMemoryNode if it exists, None otherwise.
        """
        if self.user_node_id:
            return await UserLongMemoryNode.find_one(
                user_node_id=self.user_node_id,
                category=category,
            )
        return await UserLongMemoryNode.find_one(
            user_id=self.user_id,
            category=category,
        )

    async def get_or_create_category(
        self, category: str, title: Optional[str] = None
    ) -> UserLongMemoryNode:
        """Get or create a UserLongMemoryNode for a category.

        If the node doesn't exist it is created and connected via an outgoing
        edge from this UserLongMemory node.

        Args:
            category: Stable category key (e.g., "interests").
            title:    Optional human-readable title. Defaults to CATEGORY_TITLES
                      lookup, then a capitalised version of the key.

        Returns:
            Existing or newly created UserLongMemoryNode.
        """
        if not self.user_node_id:
            from jvagent.memory.user import User as UserNode

            owner = await self.node(direction="in", node=UserNode)
            if owner:
                self.user_node_id = owner.id
                await self.save()

        existing = await self.get_category(category)
        if existing:
            return existing

        resolved_title = (
            title or CATEGORY_TITLES.get(category) or category.replace("_", " ").title()
        )
        node = await UserLongMemoryNode.create(
            user_node_id=self.user_node_id or "",
            user_id=self.user_id,
            category=category,
            title=resolved_title,
            content="",
            created_at=datetime.now(timezone.utc),
        )
        await self.connect(node)
        return node

    async def get_all_categories(self) -> List[UserLongMemoryNode]:
        """Return all connected UserLongMemoryNode objects.

        Returns:
            List of category nodes, may be empty on first use.
        """
        return await self.nodes(node=UserLongMemoryNode, direction="out")

    async def get_unindexed_categories(self) -> List[UserLongMemoryNode]:
        """Return all category nodes that have been recently updated."""
        nodes = []
        for node in await self.get_all_categories():
            if node.needs_indexing:
                nodes.append(node)
        return nodes

    async def get_content_map(self) -> Dict[str, str]:
        """Return {category: content} for all non-empty category nodes.

        Returns:
            Dict mapping category key ➜ markdown content.
        """
        result: Dict[str, str] = {}
        for node in await self.get_all_categories():
            if not node.is_empty():
                result[node.category] = node.content
        return result

    async def ensure_default_categories(self) -> List[UserLongMemoryNode]:
        """Ensure all DEFAULT_CATEGORIES exist as connected nodes.

        Creates missing category nodes with empty content. Idempotent.
        When defaults already exist, uses a single graph read plus dict lookup.

        Returns:
            List of all default UserLongMemoryNode objects.
        """
        all_nodes = await self.get_all_categories()
        by_cat = {n.category: n for n in all_nodes}
        if all(c in by_cat for c in DEFAULT_CATEGORIES):
            return [by_cat[c] for c in DEFAULT_CATEGORIES]
        nodes: List[UserLongMemoryNode] = []
        for category in DEFAULT_CATEGORIES:
            nodes.append(await self.get_or_create_category(category))
        return nodes

    async def as_markdown(self, include_empty: bool = False) -> str:
        """Render all categories to a combined markdown string.

        Args:
            include_empty: If True, include sections that have no content yet.

        Returns:
            Combined markdown text suitable for injection into prompts.
        """
        lines: List[str] = []
        for node in await self.get_all_categories():
            if not include_empty and node.is_empty():
                continue
            lines.append(f"## {node.title}")
            lines.append(node.content.strip())
            lines.append("")
        return "\n".join(lines).strip()

    # -------------------------------------------------------------------------
    # Factory / retrieval
    # -------------------------------------------------------------------------

    @classmethod
    async def get_for_user(cls, user: "User") -> Optional["UserLongMemory"]:
        """Get the UserLongMemory node for a user (via outgoing edge).

        Args:
            user: The User node to query.

        Returns:
            Connected UserLongMemory node, or None if not yet created.
        """
        return await user.node(node=UserLongMemory, direction="out")

    @classmethod
    async def get_for_user_node(cls, user_node_id: str) -> Optional["UserLongMemory"]:
        """Get UserLongMemory by owning User node's graph id (multi-agent safe)."""
        if not user_node_id:
            return None
        return await cls.find_one(user_node_id=user_node_id)

    @classmethod
    async def get_or_create_for_user(cls, user: "User") -> "UserLongMemory":
        """Get or create the UserLongMemory node for a user.

        Creates the node and connects it to the user if it doesn't exist.
        Also bootstraps default category nodes on first creation.

        Args:
            user: The User node to attach long memory to.

        Returns:
            Existing or newly created UserLongMemory node.
        """
        existing = await cls.get_for_user(user)
        if existing:
            if not existing.user_node_id:
                existing.user_node_id = user.id
                await existing.save()
            return existing

        lm = await UserLongMemory.create(
            user_node_id=user.id,
            user_id=user.user_id,
            created_at=datetime.now(timezone.utc),
        )
        await user.connect(lm)
        # Bootstrap default category nodes
        await lm.ensure_default_categories()
        return lm
