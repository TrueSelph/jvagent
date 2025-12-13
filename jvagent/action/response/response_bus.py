"""ResponseBus - Centralized response bus service (app-scoped)."""

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
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
        _message_buffers: Unified buffer for all ResponseMessage objects per interaction_id (in-order)
        _accumulation_buffers: Tracks stream sequence message_id per interaction_id (for client-side grouping)
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
        # interaction_id -> in-order ResponseMessage objects published for that interaction
        self._message_buffers: Dict[str, List[ResponseMessage]] = {}
        # interaction_id -> {"message_id": "...", "closed": bool}
        # "closed" indicates a stream sequence has ended (a "final" was published) and the next
        # stream_chunk should generate a new sequence id.
        self._accumulation_buffers: Dict[str, Dict[str, Any]] = {}
        self._observability_buffers: Dict[str, List[Dict[str, Any]]] = {}  # interaction_id -> events
        self._buffer_timestamps: Dict[str, float] = {}  # interaction_id -> creation time for TTL cleanup
        
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
        message_id: Optional[str] = None,
    ) -> ResponseMessage:
        """Publish a message to the bus.

        Args:
            session_id: Session identifier
            content: Message content
            channel: Target communication channel
            message_type: Type of message ("adhoc", "stream_chunk", "final")
            interaction_id: Parent interaction ID
            metadata: Additional metadata
            message_id: Optional message ID to reuse (for stream chunks and final messages in same sequence)

        Returns:
            Created ResponseMessage object (non-persisted)
        """
        # Handle message_id for stream chunks/final messages (for client-side grouping)
        actual_message_id = message_id
        if interaction_id and message_type in ("stream_chunk", "final"):
            seq = self._accumulation_buffers.get(interaction_id)
            if message_type == "stream_chunk":
                # If there is no active sequence (or it was closed), start a new one.
                if not seq or bool(seq.get("closed")):
                    actual_message_id = actual_message_id or f"o.ResponseMessage.{uuid.uuid4().hex[:24]}"
                    self._accumulation_buffers[interaction_id] = {
                        "message_id": actual_message_id,
                        "closed": False,
                    }
                else:
                    actual_message_id = actual_message_id or str(seq.get("message_id"))
            else:
                # "final": Prefer the active sequence id if available, otherwise keep provided id.
                if seq and seq.get("message_id"):
                    actual_message_id = actual_message_id or str(seq.get("message_id"))
                    # Mark sequence closed (do NOT delete here; finalize_interaction handles cleanup).
                    seq["closed"] = True
        
        # Create ResponseMessage with specified or auto-generated ID
        message_kwargs = {
            "session_id": session_id,
            "interaction_id": interaction_id or "",
            "content": content,
            "channel": channel,
            "message_type": message_type,
            "metadata": metadata or {},
        }
        if actual_message_id:
            message_kwargs["id"] = actual_message_id
        
        message = ResponseMessage(**message_kwargs)

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
        
        # Accumulate all messages for interaction finalization (in-order).
        # Note: We buffer "final" messages too so finalize_interaction can avoid double-final emission.
        if interaction_id:
            if interaction_id not in self._message_buffers:
                self._message_buffers[interaction_id] = []
            self._message_buffers[interaction_id].append(message)
            # Update timestamp for TTL tracking
            self._buffer_timestamps[interaction_id] = time.time()

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
        # Persist interaction.response by aggregating:
        # - Ad hoc messages (each separated by exactly two newlines)
        # - Stream chunks grouped by stream sequence (ResponseMessage.id), so multiple streams in a single
        #   interaction remain readable (each sequence separated by exactly two newlines)
        adhoc_messages: List[str] = []
        stream_sequences: Dict[str, List[str]] = {}
        stream_sequence_order: List[str] = []
        saw_final_message = False
        last_final_content: str = ""
        
        if interaction_id in self._message_buffers:
            for msg in self._message_buffers[interaction_id]:
                if msg.message_type == "final":
                    saw_final_message = True
                    if msg.content:
                        last_final_content = msg.content
                    continue

                content = msg.content
                # Only skip completely empty content (preserve whitespace/newlines otherwise)
                if content == "":
                    continue

                if msg.message_type == "stream_chunk":
                    # Group by message.id (sequence id) to avoid smashing separate streams together.
                    seq_id = msg.id or ""
                    if seq_id not in stream_sequences:
                        stream_sequences[seq_id] = []
                        stream_sequence_order.append(seq_id)
                    # Preserve exact chunk text.
                    stream_sequences[seq_id].append(content)
                elif msg.message_type == "adhoc":
                    # Ad hoc messages are separated by exactly two newlines.
                    trimmed_content = content.rstrip()
                    if trimmed_content != "":
                        adhoc_messages.append(trimmed_content)
        
        # Best-effort: reuse the stream sequence id if available (helps clients correlate final).
        sequence_message_id = None
        seq = self._accumulation_buffers.get(interaction_id)
        if seq and seq.get("message_id"):
            sequence_message_id = str(seq.get("message_id"))
        
        # Collect observability events
        observability_events = []
        if interaction_id in self._observability_buffers:
            observability_events = self._observability_buffers[interaction_id]
        
        # Aggregate:
        # - Ad hoc messages: join with exactly two newlines
        # - Main response: streamed chunks if present, otherwise existing interaction.response,
        #   otherwise buffered final content as a fallback
        # - Combine: ad hoc block first, then main response (separated by exactly two newlines)
        adhoc_block = "\n\n".join(adhoc_messages) if adhoc_messages else ""
        # Build streamed content from sequences in first-seen order; concatenate chunks within each sequence.
        stream_blocks: List[str] = []
        for seq_id in stream_sequence_order:
            seq_chunks = stream_sequences.get(seq_id) or []
            seq_text = "".join(seq_chunks)
            if seq_text != "":
                stream_blocks.append(seq_text)
        streamed_content = "\n\n".join(stream_blocks) if stream_blocks else ""

        # "Main response" must exist even when there are no stream chunks (non-streaming mode).
        main_response = streamed_content
        if not main_response:
            main_response = interaction.response or ""
        if not main_response:
            main_response = last_final_content

        aggregated_response = ""
        if adhoc_block and main_response:
            aggregated_response = f"{adhoc_block}\n\n{main_response}"
        elif adhoc_block:
            aggregated_response = adhoc_block
        elif streamed_content:
            # Only override response when we actually captured streamed chunks.
            aggregated_response = streamed_content

        # Update interaction node with aggregated response (persist ad hoc aggregation without losing main response).
        if aggregated_response:
            interaction.set_response(aggregated_response)
        
        # Add observability metrics to interaction
        if observability_events and hasattr(interaction, "observability_metrics"):
            interaction.observability_metrics = observability_events
        
        # Publish a final message (at most once) for subscribers.
        if not saw_final_message:
            await self.publish_message(
                session_id=session_id,
                content=interaction.response or "",
                channel=channel,
                message_type="final",
                interaction_id=interaction_id,
                metadata={"observability_events": len(observability_events)},
                message_id=sequence_message_id,
            )
        
        # Clear accumulation buffers and timestamps (ephemeral - cleared after finalization)
        self._accumulation_buffers.pop(interaction_id, None)
        if interaction_id in self._message_buffers:
            del self._message_buffers[interaction_id]
        if interaction_id in self._observability_buffers:
            del self._observability_buffers[interaction_id]
        if interaction_id in self._buffer_timestamps:
            del self._buffer_timestamps[interaction_id]
        
        # Keep the session queue ephemeral by dropping older messages.
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
        """Drop old messages from the session queue.

        Args:
            session_id: Session ID to clean up
        """
        if session_id not in self._session_queues:
            return
        
        queue = self._session_queues[session_id]
        cutoff_dt = datetime.now(timezone.utc) - timedelta(minutes=5)
        
        # ResponseMessage.timestamp is timezone-aware datetime; keep any messages newer than cutoff.
        filtered_queue = [msg for msg in queue if msg.timestamp and msg.timestamp > cutoff_dt]
        
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
            self._message_buffers.pop(interaction_id, None)
            self._observability_buffers.pop(interaction_id, None)
            self._buffer_timestamps.pop(interaction_id, None)

