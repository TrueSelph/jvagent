"""Log entry models for interaction logging."""

from datetime import datetime, timezone
from typing import Any, Dict

from jvspatial.core import Object
from jvspatial.core.annotations import attribute, compound_index


@compound_index([("context.app_id", 1), ("context.logged_at", -1)], name="app_logged_at")
@compound_index([("context.app_id", 1), ("context.user_id", 1), ("context.logged_at", -1)], name="app_user_logged_at")
@compound_index([("context.app_id", 1), ("context.conversation_id", 1), ("context.logged_at", -1)], name="app_conv_logged_at")
@compound_index([("context.agent_id", 1), ("context.logged_at", -1)], name="agent_logged_at")
@compound_index([("context.agent_id", 1), ("context.user_id", 1), ("context.logged_at", -1)], name="agent_user_logged_at")
class InteractionLog(Object):
    """Log entry for a complete interaction.

    Stores the complete exported structure of an interaction for logging purposes.
    Each log entry is mapped by application ID and agent ID, and includes all interaction data.

    Attributes:
        app_id: Application node ID
        agent_id: Agent node ID
        interaction_id: Original interaction ID
        conversation_id: Parent conversation ID
        session_id: Session identifier
        user_id: User ID
        logged_at: Timestamp when the interaction was logged
        interaction_data: Complete interaction export structure
    """

    app_id: str = attribute(
        indexed=True, default="", description="Application node ID"
    )
    agent_id: str = attribute(
        indexed=True, default="", description="Agent node ID"
    )
    interaction_id: str = attribute(
        indexed=True, default="", description="Original interaction ID"
    )
    conversation_id: str = attribute(
        indexed=True, default="", description="Parent conversation ID"
    )
    session_id: str = attribute(
        default="", description="Session identifier"
    )
    user_id: str = attribute(
        indexed=True, default="", description="User ID"
    )
    logged_at: datetime = attribute(
        indexed=True,
        index_direction=-1,
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when the interaction was logged",
    )
    interaction_data: Dict[str, Any] = attribute(
        default_factory=dict,
        description="Complete interaction export structure",
    )

