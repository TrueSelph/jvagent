"""Memory manager node for agent memory, user, and conversation management."""

from typing import Any, Dict, Optional
from datetime import datetime

from jvspatial.core import Node
from jvspatial.core.annotations import attribute


class Memory(Node):
    """Memory system node for managing users, conversations, and collections.
    
    The Memory node manages all memory-related entities for an agent including:
    - User nodes (representing end-users interacting with the agent)
    - Conversation nodes (representing conversation sessions)
    - Collection nodes (representing knowledge collections)
    
    Attributes:
        total_users: Total number of users
        total_conversations: Total number of conversations
        total_collections: Total number of collections
        last_cleanup: Timestamp of last cleanup operation
    """
    
    # Counters
    total_users: int = attribute(default=0, description="Total number of users")
    total_conversations: int = attribute(default=0, description="Total number of conversations")
    total_collections: int = attribute(default=0, description="Total number of collections")
    
    # Maintenance
    last_cleanup: Optional[datetime] = attribute(
        default=None,
        description="Timestamp of last cleanup operation"
    )

