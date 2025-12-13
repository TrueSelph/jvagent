"""ResponseBus - Centralized response bus service (app-scoped)."""

import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from jvagent.action.response.message import ResponseMessage

logger = logging.getLogger(__name__)


class ResponseBus:
    """Centralized response bus service (app-scoped singleton).

    The ResponseBus manages message queues per session and handles subscriptions
    from channel adapters. It provides a publishing interface for InteractActions
    to send adhoc messages and stream chunks.

    The bus is app-scoped (single shared instance across all agents) and manages
    ephemeral message queues that are cleared after delivery. Isolation is provided
    via session_id and interaction_id tagging.

    Attributes:
        _session_queues: Ephemeral message queues per session
        _subscribers: Channel adapters subscribed to sessions
        _subscriber_preferences: Subscription preferences per callback (receive_chunks)
        _accumulation_buffers: Buffers for accumulating stream chunks per interaction_id
        _observability_buffers: Buffers for observability events per interaction_id
        _lock: Async lock for thread-safe operations
    """

    _instance: Optional["ResponseBus"] = None
    _lock: asyncio.Lock = asyncio.Lock()

    def __init__(self):
        """Initialize ResponseBus (app-scoped singleton).

        Note: Use get_instance() to obtain the singleton instance.
        """
        self._session_queues: Dict[str, List[ResponseMessage]] = {}
        self._subscribers: Dict[str, List[Callable[[ResponseMessage], Any]]] = {}
        self._subscriber_preferences: Dict[Callable[[ResponseMessage], Any], Dict[str, Any]] = {}
        self._accumulation_buffers: Dict[str, List[str]] = {}  # interaction_id -> chunks
        self._observability_buffers: Dict[str, List[Dict[str, Any]]] = {}  # interaction_id -> events
        self._buffer_timestamps: Dict[str, float] = {}  # interaction_id -> creation time for TTL cleanup
        self._lock = asyncio.Lock()
        
        # Configuration
        self._max_session_queue_size = 1000  # Bounded storage per session
        self._buffer_ttl_seconds = 3600  # 1 hour TTL for accumulation/observability buffers

    @classmethod
    async def get_instance(cls) -> "ResponseBus":
        """Get the singleton ResponseBus instance.

        Returns:
            ResponseBus singleton instance
        """
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

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
            agent_id="",  # No longer agent-scoped
            session_id=session_id,
            interaction_id=interaction_id or "",
            content=content,
            channel=channel,
            message_type=message_type,
            metadata=metadata or {},
        )

        # Add to ephemeral session queue (bounded storage)
        if session_id not in self._session_queues:
            self._session_queues[session_id] = []
        queue = self._session_queues[session_id]
        queue.append(message)
        # Enforce bounded storage - remove oldest messages if over limit
        if len(queue) > self._max_session_queue_size:
            queue.pop(0)

        # Notify subscribers based on preferences
        if session_id in self._subscribers:
            for callback in self._subscribers[session_id]:
                # Check subscription preferences
                prefs = self._subscriber_preferences.get(callback, {})
                receive_chunks = prefs.get("receive_chunks", False)
                
                # Skip stream_chunk messages if subscriber doesn't want chunks
                if message_type == "stream_chunk" and not receive_chunks:
                    continue
                
                # Always dispatch final and adhoc messages
                try:
                    if callable(callback):
                        # Robustly handle sync + async callables (including wrapped/bound)
                        result = callback(message)
                        if asyncio.iscoroutine(result):
                            asyncio.create_task(self._safe_awaitable(result))
                except Exception as e:
                    logger.error(
                        f"Error notifying subscriber for session {session_id}: {e}",
                        exc_info=True,
                    )
        
        # Accumulate stream chunks (with timestamp for TTL)
        if message_type == "stream_chunk" and interaction_id:
            if interaction_id not in self._accumulation_buffers:
                self._accumulation_buffers[interaction_id] = []
                self._buffer_timestamps[interaction_id] = time.time()
            self._accumulation_buffers[interaction_id].append(content)

        return message

    async def _safe_awaitable(self, awaitable: Any) -> None:
        """Safely await a coroutine/awaitable with error handling."""
        try:
            await awaitable
        except Exception as e:
            logger.error(
                f"Error in subscriber callback: {e}",
                exc_info=True,
            )

    async def subscribe(
        self,
        session_id: str,
        callback: Callable[[ResponseMessage], Any],
        receive_chunks: bool = False,
    ) -> None:
        """Subscribe to messages for a session.

        Args:
            session_id: Session identifier
            callback: Callback function to call when messages are published
            receive_chunks: If True, receive stream_chunk messages. If False, only receive
                          final and adhoc messages. Default: False
        """
        if session_id not in self._subscribers:
            self._subscribers[session_id] = []
        # Prevent duplicate subscriptions for same callback
        if callback not in self._subscribers[session_id]:
            self._subscribers[session_id].append(callback)
        self._subscriber_preferences[callback] = {"receive_chunks": receive_chunks}

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
        
        # Clean up preferences
        if callback in self._subscriber_preferences:
            del self._subscriber_preferences[callback]

    async def publish_observability(
        self,
        interaction_id: str,
        event_type: str,
        data: Dict[str, Any],
    ) -> None:
        """Publish observability event (model calls, embeddings, etc.).

        Only emits when interaction_id is available.

        Args:
            interaction_id: Interaction ID this event belongs to
            event_type: Type of event ("model_call", "embedding_call", "action_metric")
            data: Event data (duration, tokens, model name, provider, cost estimates, etc.)
        """
        if not interaction_id:
            return  # Only emit when interaction_id is available
        
        if interaction_id not in self._observability_buffers:
            self._observability_buffers[interaction_id] = []
            # Set timestamp if not already set (may have been set by accumulation)
            if interaction_id not in self._buffer_timestamps:
                self._buffer_timestamps[interaction_id] = time.time()
        
        event = {
            "event_type": event_type,
            "data": data,
            "timestamp": time.time(),
        }
        self._observability_buffers[interaction_id].append(event)

    async def finalize_interaction(
        self,
        interaction_id: str,
        interaction: Any,
        session_id: str,
        channel: str = "default",
    ) -> None:
        """Finalize an interaction by accumulating streamed data and updating the interaction node.

        Args:
            interaction_id: Interaction ID to finalize
            interaction: Interaction node instance to update
            session_id: Session ID for publishing final message
            channel: Channel for final message
        """
        # Aggregate stream chunks
        accumulated_content = ""
        if interaction_id in self._accumulation_buffers:
            accumulated_content = "".join(self._accumulation_buffers[interaction_id])
        
        # Collect observability events
        observability_events = []
        if interaction_id in self._observability_buffers:
            observability_events = self._observability_buffers[interaction_id]
        
        # Update interaction node
        if accumulated_content:
            # Store accumulated response
            if hasattr(interaction, "accumulated_response"):
                interaction.accumulated_response = accumulated_content
            # Also set as response if not already set
            if not interaction.response:
                interaction.set_response(accumulated_content)
        
        # Add observability metrics to interaction
        if observability_events and hasattr(interaction, "observability_metrics"):
            interaction.observability_metrics = observability_events
        
        # Publish final message with complete content (even if empty, to signal completion)
        final_content = accumulated_content or (interaction.response if hasattr(interaction, "response") and interaction.response else "")
        await self.publish_message(
            session_id=session_id,
            content=final_content,
            channel=channel,
            message_type="final",
            interaction_id=interaction_id,
            metadata={"observability_events": len(observability_events)},
        )
        
        # Clear accumulation buffers and timestamps (ephemeral - cleared after finalization)
        if interaction_id in self._accumulation_buffers:
            del self._accumulation_buffers[interaction_id]
        if interaction_id in self._observability_buffers:
            del self._observability_buffers[interaction_id]
        if interaction_id in self._buffer_timestamps:
            del self._buffer_timestamps[interaction_id]
        
        # Clear session queue after finalization to keep queues ephemeral
        # Only clear if this was the last interaction for the session
        # (In practice, we may want to keep some messages for a short time, but for strict ephemeral behavior, clear immediately)
        # Note: This is aggressive - you may want to keep messages until session is explicitly cleared
        # For now, we'll clear messages older than 5 minutes to balance ephemeral behavior with debugging needs
        await self._cleanup_old_session_messages(session_id)

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
            # Remove preferences for callbacks subscribed to this session
            for cb in self._subscribers[session_id]:
                self._subscriber_preferences.pop(cb, None)
            del self._subscribers[session_id]

    async def get_all_sessions(self) -> List[str]:
        """Get all active session IDs.

        Returns:
            List of session IDs with active queues
        """
        return list(self._session_queues.keys())
    
    async def _cleanup_old_session_messages(self, session_id: str) -> None:
        """Clean up old messages from session queue (ephemeral behavior).

        Args:
            session_id: Session ID to clean up
        """
        if session_id not in self._session_queues:
            return
        
        current_time = time.time()
        queue = self._session_queues[session_id]
        # Remove messages older than 5 minutes
        cutoff_time = current_time - 300  # 5 minutes
        
        filtered_queue = [
            msg for msg in queue
            if hasattr(msg, "timestamp") and msg.timestamp
            and (msg.timestamp.timestamp() if hasattr(msg.timestamp, "timestamp") else time.time()) > cutoff_time
        ]
        
        if len(filtered_queue) < len(queue):
            self._session_queues[session_id] = filtered_queue
            logger.debug(f"Cleaned up {len(queue) - len(filtered_queue)} old messages from session {session_id}")

    async def cleanup_expired_buffers(self) -> None:
        """Clean up expired accumulation and observability buffers (TTL cleanup).

        This should be called periodically to prevent memory leaks from interactions
        that never finalize (e.g., due to crashes or cancellations).
        """
        current_time = time.time()
        expired_interaction_ids = [
            interaction_id
            for interaction_id, timestamp in self._buffer_timestamps.items()
            if current_time - timestamp > self._buffer_ttl_seconds
        ]
        
        for interaction_id in expired_interaction_ids:
            logger.debug(f"Cleaning up expired buffers for interaction {interaction_id}")
            self._accumulation_buffers.pop(interaction_id, None)
            self._observability_buffers.pop(interaction_id, None)
            self._buffer_timestamps.pop(interaction_id, None)

