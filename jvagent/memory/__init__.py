"""Memory system for jvagent.

This module provides the memory management system including:
- Memory: Manager node for user, conversation, and collection management
- User: User node representing users interacting with the agent
- Conversation: Conversation node for session-based conversations
- Interaction: Interaction node for single exchanges

Entity Relationships (Edge-Connected Nodes):
    Memory (Node)
        └── [edge] ──► User (Node)
                          └── [edge] ──► Conversation (Node)
                                              └── [edge] ──► Interaction (Node)

Cascade Delete Behavior:
    - Delete User → Cascades to all Conversations → Cascades to all Interactions
    - Delete Conversation → Cascades to all Interactions
    - Delete Memory → Cascades to all Users → All Conversations → All Interactions
"""

from jvagent.memory.artifact import Artifact, Artifacts
from jvagent.memory.conversation import Conversation
from jvagent.memory.interaction import Interaction
from jvagent.memory.manager import Memory
from jvagent.memory.user import User

__all__ = ["Memory", "User", "Conversation", "Interaction", "Artifact", "Artifacts"]
