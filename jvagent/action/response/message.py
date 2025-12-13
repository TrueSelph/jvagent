"""ResponseMessage object for representing individual response messages (non-persisted)."""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from jvspatial.core import Object
from jvspatial.core.annotations import attribute


class ResponseMessage(Object):
    """Non-persisted object representing individual response messages (adhoc or streamed chunks).

    ResponseMessage objects represent ephemeral messages that are:
    - Stored only in memory within the ResponseBus session queues
    - Never persisted to the database
    - Cleared after session completion

    Message types:
    - Adhoc responses (multiple responses to same utterance)
    - Stream chunks (parts of a streamed response)
    - Final responses (consolidated end-of-walk response)
    - Observability events (model_call, embedding_call, action_metric)

    Attributes:
        agent_id: Agent identifier this message belongs to
        session_id: Session identifier
        interaction_id: Parent interaction ID
        message_type: Type of message ("adhoc", "stream_chunk", "final")
        content: Message content
        channel: Target channel
        metadata: Additional metadata
        timestamp: When message was created
        delivered: Whether message was delivered
    """

    agent_id: str = attribute(
        default="", description="Agent identifier this message belongs to"
    )
    session_id: str = attribute(
        default="", description="Session identifier"
    )
    interaction_id: str = attribute(
        default="", description="Parent interaction ID"
    )
    message_type: str = attribute(
        default="adhoc",
        description='Type of message: "adhoc", "stream_chunk", or "final"',
    )
    content: str = attribute(default="", description="Message content")
    channel: str = attribute(
        default="default", description="Target communication channel"
    )
    metadata: Dict[str, Any] = attribute(
        default_factory=dict, description="Additional metadata"
    )
    observability_data: Optional[Dict[str, Any]] = attribute(
        default=None, description="Structured observability metrics (for observability message types)"
    )
    timestamp: datetime = attribute(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When message was created",
    )
    delivered: bool = attribute(
        default=False, description="Whether message was delivered"
    )

    def mark_delivered(self) -> None:
        """Mark the message as delivered."""
        self.delivered = True

    def to_dict(self) -> Dict[str, Any]:
        """Convert message to dictionary format.

        Returns:
            Dictionary representation of the message
        """
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "interaction_id": self.interaction_id,
            "message_type": self.message_type,
            "content": self.content,
            "channel": self.channel,
            "metadata": self.metadata,
            "observability_data": self.observability_data,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "delivered": self.delivered,
        }

