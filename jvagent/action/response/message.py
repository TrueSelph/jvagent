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
        session_id: Session identifier
        interaction_id: Parent interaction ID
        message_type: Type of message ("adhoc", "stream_chunk", "final")
        content: Message content
        channel: Target channel
        metadata: Additional metadata
        timestamp: When message was created
        delivered: Whether message was delivered
    """

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

    async def save(self) -> "ResponseMessage":
        """Override save() to prevent ResponseMessage from being persisted.
        
        ResponseMessage objects are ephemeral and should never be saved to the database.
        They are only stored in memory within the ResponseBus session queues.
        
        Returns:
            Self (for chaining)
        
        Raises:
            RuntimeError: Always raises, as ResponseMessage should never be saved
        """
        raise RuntimeError(
            "ResponseMessage objects are ephemeral and cannot be saved to the database. "
            "They are only stored in memory within the ResponseBus session queues."
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert message to dictionary format.
        
        Observability data, delivered status, and timestamp are conditionally omitted:
        - observability_data: Never included (keeps payloads lightweight)
        - delivered: Only meaningful for channel adapters, not SSE streaming
        - timestamp: Omitted for stream_chunk messages (not useful - chunks arrive in order,
          timestamp only needed once when creating message bubble, client can timestamp on receipt)

        Returns:
            Dictionary representation of the message
        """
        result: Dict[str, Any] = {
            "id": self.id,
            "session_id": self.session_id,
            "interaction_id": self.interaction_id,
            "message_type": self.message_type,
            "content": self.content,
            "channel": self.channel,
            "metadata": self.metadata,
        }
        
        # Only include timestamp for non-stream-chunk messages
        # Stream chunks arrive in order, timestamp is only used once (first chunk),
        # and client can timestamp on receipt for better UX
        if self.message_type != "stream_chunk":
            result["timestamp"] = self.timestamp.isoformat() if self.timestamp else None
        
        return result

