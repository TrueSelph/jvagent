"""ResponseBus - Centralized response bus service (agent-scoped)."""

import logging
from typing import Any, Callable, Dict, List, Optional

from jvagent.action.response.message import ResponseMessage

logger = logging.getLogger(__name__)


class ResponseBus:
    """Centralized response bus service (agent-scoped).

    The ResponseBus manages message queues per session and handles subscriptions
    from channel adapters. It provides a publishing interface for InteractActions
    to send adhoc messages and stream chunks.

    The bus is agent-specific (one instance per agent, similar to InteractWalker)
    and manages ephemeral message queues that are cleared after delivery.

    Attributes:
        agent_id: Agent identifier this bus belongs to
        _session_queues: Ephemeral message queues per session
        _subscribers: Channel adapters subscribed to sessions
    """

    def __init__(self, agent_id: str):
        """Initialize ResponseBus for an agent.

        Args:
            agent_id: Agent identifier
        """
        self.agent_id = agent_id
        self._session_queues: Dict[str, List[ResponseMessage]] = {}
        self._subscribers: Dict[str, List[Callable[[ResponseMessage], Any]]] = {}

    async def publish_message(
        self,
        session_id: str,
        content: str,
        channel: str = "default",
        message_type: str = "adhoc",
        interaction_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ResponseMessage:
        """Publish a message to the bus.

        Args:
            session_id: Session identifier
            content: Message content
            channel: Target communication channel
            message_type: Type of message ("adhoc", "stream_chunk", "final")
            interaction_id: Parent interaction ID
            metadata: Additional metadata

        Returns:
            Created ResponseMessage object (non-persisted)
        """
        message = ResponseMessage(
            agent_id=self.agent_id,
            session_id=session_id,
            interaction_id=interaction_id or "",
            content=content,
            channel=channel,
            message_type=message_type,
            metadata=metadata or {},
        )

        # Add to ephemeral session queue
        if session_id not in self._session_queues:
            self._session_queues[session_id] = []
        self._session_queues[session_id].append(message)

        # Notify subscribers
        if session_id in self._subscribers:
            for callback in self._subscribers[session_id]:
                try:
                    if callable(callback):
                        # Check if callback is async
                        import asyncio

                        if asyncio.iscoroutinefunction(callback):
                            await callback(message)
                        else:
                            callback(message)
                except Exception as e:
                    logger.error(
                        f"Error notifying subscriber for session {session_id}: {e}",
                        exc_info=True,
                    )

        return message

    async def publish_chunk(
        self,
        session_id: str,
        chunk: str,
        interaction_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ResponseMessage:
        """Publish a stream chunk to the bus.

        Args:
            session_id: Session identifier
            chunk: Stream chunk content
            interaction_id: Parent interaction ID
            metadata: Additional metadata

        Returns:
            Created ResponseMessage object (non-persisted)
        """
        return await self.publish_message(
            session_id=session_id,
            content=chunk,
            message_type="stream_chunk",
            interaction_id=interaction_id,
            metadata=metadata,
        )

    async def subscribe(
        self, session_id: str, callback: Callable[[ResponseMessage], Any]
    ) -> None:
        """Subscribe to messages for a session.

        Args:
            session_id: Session identifier
            callback: Callback function to call when messages are published
        """
        if session_id not in self._subscribers:
            self._subscribers[session_id] = []
        self._subscribers[session_id].append(callback)

    async def unsubscribe(
        self, session_id: str, callback: Callable[[ResponseMessage], Any]
    ) -> None:
        """Unsubscribe from messages for a session.

        Args:
            session_id: Session identifier
            callback: Callback function to remove
        """
        if session_id in self._subscribers:
            try:
                self._subscribers[session_id].remove(callback)
            except ValueError:
                pass  # Callback not in list

    async def get_messages(self, session_id: str) -> List[ResponseMessage]:
        """Get messages for a session.

        Args:
            session_id: Session identifier

        Returns:
            List of ResponseMessage objects for the session
        """
        return self._session_queues.get(session_id, [])

    async def clear_session(self, session_id: str) -> None:
        """Clear ephemeral messages after delivery.

        Args:
            session_id: Session identifier
        """
        if session_id in self._session_queues:
            del self._session_queues[session_id]
        if session_id in self._subscribers:
            del self._subscribers[session_id]

    async def get_all_sessions(self) -> List[str]:
        """Get all active session IDs.

        Returns:
            List of session IDs with active queues
        """
        return list(self._session_queues.keys())

