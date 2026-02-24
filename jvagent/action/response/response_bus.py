"""ResponseBus - Centralized response bus service (app-scoped)."""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from jvagent.action.response.message import ResponseMessage
from jvagent.core.app import App

logger = logging.getLogger(__name__)


@dataclass
class AdhocAccumulator:
    """Accumulates streaming adhoc chunks per interaction until streaming_complete."""

    chunks: List[str] = field(default_factory=list)
    channel: str = "default"
    user_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    message_id: str = ""
    session_id: str = ""
    interaction_id: str = ""
    started_at: float = field(default_factory=time.time)


# Forward declaration for type hints
if TYPE_CHECKING:
    from jvagent.action.response.channel_adapter import ChannelAdapter
    from jvagent.action.response.channel_filter import ChannelFilter


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
        _adhoc_accumulation: Streaming adhoc chunks per interaction_id until streaming_complete (AdhocAccumulator)
        _lock: Async lock for thread-safe operations
    """

    # Cleanup configuration
    CLEANUP_INTERVAL_SECONDS = 60  # Max once per 60s for lazy cleanup
    BUFFER_TTL_SECONDS = 3600  # 1 hour TTL for message/observability buffers
    ACCUMULATOR_TIMEOUT_SECONDS = 120  # 2 min timeout for incomplete streams

    _instance: Optional["ResponseBus"] = None
    _lock: asyncio.Lock = asyncio.Lock()

    def __init__(self):
        """Initialize ResponseBus (app-scoped singleton).

        Note: Use get_instance() to obtain the singleton instance.
        This should only be called once via get_instance().
        """
        self._session_queues: Dict[str, List[ResponseMessage]] = {}
        self._subscribers: Dict[str, List[Callable[[ResponseMessage], Any]]] = {}
        # O(1) subscriber lookup set: {session_id: {id(callback), ...}}
        # Used for fast duplicate checking during subscribe()
        self._subscriber_ids: Dict[str, set] = {}
        self._subscriber_preferences: Dict[
            Callable[[ResponseMessage], Any], Dict[str, Any]
        ] = {}
        # interaction_id -> in-order ResponseMessage objects published for that interaction
        self._message_buffers: Dict[str, List[ResponseMessage]] = {}
        self._buffer_timestamps: Dict[str, float] = (
            {}
        )  # interaction_id -> creation time for TTL cleanup

        # Channel adapter registry: maps channel name -> single adapter instance
        self._channel_adapters: Dict[str, "ChannelAdapter"] = (
            {}
        )  # channel -> single ChannelAdapter instance

        # Channel filter registry: list of filters sorted by priority (lower priority executes first)
        self._channel_filters: List["ChannelFilter"] = []

        # Streaming adhoc accumulation: interaction_id -> AdhocAccumulator (chunks until streaming_complete)
        self._adhoc_accumulation: Dict[str, AdhocAccumulator] = {}

        # Configuration
        self._max_session_queue_size = 1000  # Bounded storage per session
        self._buffer_ttl_seconds = (
            3600  # 1 hour TTL for accumulation/observability buffers
        )

        # Cleanup state
        self._last_cleanup_time: float = 0.0

    async def _get_now(self) -> datetime:
        """Current datetime in app timezone, or UTC if App unavailable."""
        app = await App.get()
        if app:
            return await app.now()
        return datetime.now(timezone.utc)

    @classmethod
    async def get_instance(cls) -> "ResponseBus":
        """Get the singleton ResponseBus instance.

        This ensures only ONE ResponseBus instance exists across the entire application.
        The instance is created on first access and reused for all subsequent calls.

        Returns:
            ResponseBus singleton instance
        """
        if cls._instance is None:
            async with cls._lock:
                # Double-check pattern to prevent race conditions
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _maybe_cleanup(self) -> None:
        """Lazy cleanup - evict expired entries only. Runs at most once per CLEANUP_INTERVAL_SECONDS.

        IMPORTANT: Only evicts by TTL/timeout, never by count. Size-based eviction is unsafe
        for concurrent users - it could evict an active user's accumulator mid-stream.
        """
        now = time.time()
        if now - self._last_cleanup_time < self.CLEANUP_INTERVAL_SECONDS:
            return
        self._last_cleanup_time = now

        # Evict expired accumulators (incomplete streams older than timeout)
        expired_acc = [
            k
            for k, v in self._adhoc_accumulation.items()
            if now - v.started_at > self.ACCUMULATOR_TIMEOUT_SECONDS
        ]
        for k in expired_acc:
            logger.debug(f"Evicting expired accumulator for interaction {k}")
            self._adhoc_accumulation.pop(k, None)

        # Evict expired buffers (interactions never finalized within TTL)
        expired_buf = [
            k
            for k, ts in self._buffer_timestamps.items()
            if now - ts > self.BUFFER_TTL_SECONDS
        ]
        for k in expired_buf:
            logger.debug(f"Evicting expired buffers for interaction {k}")
            self._message_buffers.pop(k, None)
            self._buffer_timestamps.pop(k, None)

    def _get_or_create_accumulator(
        self,
        interaction_id: str,
        session_id: str,
        channel: str,
        user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AdhocAccumulator:
        """Get or create AdhocAccumulator for this interaction (new sequence after commit)."""
        if interaction_id not in self._adhoc_accumulation:
            message_id = f"o.ResponseMessage.{uuid.uuid4().hex[:24]}"
            self._adhoc_accumulation[interaction_id] = AdhocAccumulator(
                chunks=[],
                channel=channel,
                user_id=user_id,
                metadata=metadata or {},
                message_id=message_id,
                session_id=session_id,
                interaction_id=interaction_id,
            )
        return self._adhoc_accumulation[interaction_id]

    async def _enqueue_and_notify(
        self, message: ResponseMessage, session_id: str
    ) -> None:
        """Add message to session queue, enforce bound, notify subscribers.
        Awaits async callbacks so SSE consumer receives messages before walk_task.done() check.
        """
        if session_id not in self._session_queues:
            self._session_queues[session_id] = []
        queue = self._session_queues[session_id]
        queue.append(message)
        if len(queue) > self._max_session_queue_size:
            queue.pop(0)
        if session_id in self._subscribers:
            for callback in self._subscribers[session_id]:
                prefs = self._subscriber_preferences.get(callback, {})
                receive_chunks = prefs.get("receive_chunks", False)
                if message.message_type == "stream_chunk" and not receive_chunks:
                    continue
                try:
                    if callable(callback):
                        result = callback(message)
                        if asyncio.iscoroutine(result):
                            await result
                except Exception as e:
                    logger.error(
                        f"Error notifying subscriber for session {session_id}: {e}",
                        exc_info=True,
                    )

    def _append_to_message_buffers(
        self, interaction_id: str, message: ResponseMessage
    ) -> None:
        """Append message to interaction message buffer and update TTL timestamp."""
        if interaction_id not in self._message_buffers:
            self._message_buffers[interaction_id] = []
        self._message_buffers[interaction_id].append(message)
        self._buffer_timestamps[interaction_id] = time.time()

    async def publish(
        self,
        session_id: str,
        content: str,
        channel: str,
        stream: bool = False,
        interaction_id: Optional[str] = None,
        interaction: Optional[Any] = None,
        user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        streaming_complete: bool = True,
        transient: bool = False,
    ) -> ResponseMessage:
        """Publish adhoc content. Stream mode and streaming_complete control accumulation and delivery.

        Non-streaming (stream=False): Apply filters, send to adapter, append to interaction.response, notify.
        Streaming (stream=True), streaming_complete=False: Accumulate chunk, emit stream_chunk to subscribers.
        Streaming (stream=True), streaming_complete=True: Flush accumulator: filters/adapters on full content,
        set interaction.response, emit final signal, clear accumulator.
        Simulated streaming: When stream=True with streaming_complete=True and non-empty content (whole content
        in one shot), content is split using language-model tokenization (see chunking.chunk_text_by_lm_tokens)
        and emitted as stream_chunks so the client sees token-by-token delivery.

        Args:
            session_id: Session identifier
            content: Message content (or chunk when streaming)
            channel: Target channel
            stream: From visitor.stream; if True, content may be streamed in chunks
            interaction_id: Parent interaction ID
            interaction: Optional interaction instance (avoids DB lookup for append)
            user_id: User identifier (recipient)
            metadata: Additional metadata
            streaming_complete: True when this is the last chunk (or single message). Only relevant when stream=True.
            transient: If True, skip appending content to interaction.response.
                Use for transient messages (e.g., canned responses, typing indicators) that
                shouldn't be recorded as the interaction's final response. Default: False.

        Returns:
            Created ResponseMessage (stream_chunk, adhoc, or final depending on path).
        """
        # Throttled TTL-based cleanup
        self._maybe_cleanup()

        now = await self._get_now()

        if not stream:
            # Non-streaming: immediate filters, adapter, accumulation, one adhoc message
            message = ResponseMessage(
                session_id=session_id,
                user_id=user_id or "",
                interaction_id=interaction_id or "",
                content=content,
                channel=channel,
                message_type="adhoc",
                metadata=metadata or {},
                timestamp=now,
            )
            filter_ok = await self._apply_channel_filters(message, channel)
            if filter_ok:
                if channel in self._channel_adapters:
                    await self._send_to_adapter(
                        self._channel_adapters[channel], message
                    )
                if (interaction_id or interaction) and message.content:
                    if interaction is not None and not transient:
                        await self._append_to_interaction_response_impl(
                            interaction=interaction,
                            message_type="adhoc",
                            content=message.content,
                        )
            await self._enqueue_and_notify(message, session_id)
            if interaction_id:
                self._append_to_message_buffers(interaction_id, message)
            return message

        # Streaming path
        if not interaction_id:
            # No interaction: treat as single adhoc (no accumulation)
            message = ResponseMessage(
                session_id=session_id,
                user_id=user_id or "",
                interaction_id=interaction_id or "",
                content=content,
                channel=channel,
                message_type="adhoc",
                metadata=metadata or {},
                timestamp=now,
            )
            filter_ok = await self._apply_channel_filters(message, channel)
            if filter_ok:
                if channel in self._channel_adapters:
                    await self._send_to_adapter(
                        self._channel_adapters[channel], message
                    )
                if interaction and not transient:
                    await self._append_to_interaction_response_impl(
                        interaction=interaction,
                        message_type="adhoc",
                        content=message.content,
                    )
            await self._enqueue_and_notify(message, session_id)
            return message

        # Auto-detect whole content in stream mode: one call with stream=True, streaming_complete=True, non-empty content
        if stream and streaming_complete and content:
            from jvagent.action.response.chunking import chunk_text_by_lm_tokens

            acc = self._get_or_create_accumulator(
                interaction_id=interaction_id,
                session_id=session_id,
                channel=channel,
                user_id=user_id,
                metadata=metadata,
            )
            for chunk in chunk_text_by_lm_tokens(content):
                acc.chunks.append(chunk)
                chunk_message = ResponseMessage(
                    id=acc.message_id,
                    session_id=session_id,
                    user_id=user_id or "",
                    interaction_id=interaction_id,
                    content=chunk,
                    channel=channel,
                    message_type="stream_chunk",
                    metadata=metadata or {},
                    timestamp=now,
                )
                await self._enqueue_and_notify(chunk_message, session_id)
                self._append_to_message_buffers(interaction_id, chunk_message)

            full_content = "".join(acc.chunks)
            flush_message = ResponseMessage(
                session_id=session_id,
                user_id=acc.user_id or "",
                interaction_id=interaction_id,
                content=full_content,
                channel=acc.channel,
                message_type="adhoc",
                metadata=acc.metadata or {},
                timestamp=now,
            )
            filter_ok = await self._apply_channel_filters(flush_message, acc.channel)
            if filter_ok:
                if acc.channel in self._channel_adapters:
                    await self._send_to_adapter(
                        self._channel_adapters[acc.channel], flush_message
                    )
                if interaction is not None and flush_message.content and not transient:
                    await self._append_to_interaction_response_impl(
                        interaction=interaction,
                        message_type="adhoc",
                        content=flush_message.content,
                    )
            self._append_to_message_buffers(interaction_id, flush_message)
            final_message = ResponseMessage(
                id=acc.message_id,
                session_id=session_id,
                user_id=acc.user_id or "",
                interaction_id=interaction_id,
                content="",
                channel=acc.channel,
                message_type="final",
                metadata=acc.metadata or {},
                timestamp=now,
            )
            await self._enqueue_and_notify(final_message, session_id)
            self._append_to_message_buffers(interaction_id, final_message)
            self._adhoc_accumulation.pop(interaction_id, None)
            return final_message

        try:
            acc = self._get_or_create_accumulator(
                interaction_id=interaction_id,
                session_id=session_id,
                channel=channel,
                user_id=user_id,
                metadata=metadata,
            )
            acc.chunks.append(content)

            now = await self._get_now()
            if not streaming_complete:
                # Emit chunk to subscribers only
                chunk_message = ResponseMessage(
                    id=acc.message_id,
                    session_id=session_id,
                    user_id=user_id or "",
                    interaction_id=interaction_id,
                    content=content,
                    channel=channel,
                    message_type="stream_chunk",
                    metadata=metadata or {},
                    timestamp=now,
                )
                await self._enqueue_and_notify(chunk_message, session_id)
                self._append_to_message_buffers(interaction_id, chunk_message)
                return chunk_message

            # streaming_complete=True: flush accumulator
            full_content = "".join(acc.chunks)
            flush_message = ResponseMessage(
                session_id=session_id,
                user_id=acc.user_id or "",
                interaction_id=interaction_id,
                content=full_content,
                channel=acc.channel,
                message_type="adhoc",
                metadata=acc.metadata or {},
                timestamp=now,
            )
            filter_ok = await self._apply_channel_filters(flush_message, acc.channel)
            if filter_ok:
                if acc.channel in self._channel_adapters:
                    await self._send_to_adapter(
                        self._channel_adapters[acc.channel], flush_message
                    )
                if full_content and (interaction or interaction_id):
                    if interaction is not None and not transient:
                        await self._append_to_interaction_response_impl(
                            interaction=interaction,
                            message_type="adhoc",
                            content=flush_message.content,
                        )
            # Final signal (empty content) for end-of-stream
            final_message = ResponseMessage(
                id=acc.message_id,
                session_id=session_id,
                user_id=acc.user_id or "",
                interaction_id=interaction_id,
                content="",
                channel=acc.channel,
                message_type="final",
                metadata=acc.metadata or {},
                timestamp=now,
            )
            await self._enqueue_and_notify(final_message, session_id)
            self._append_to_message_buffers(interaction_id, final_message)
            self._adhoc_accumulation.pop(interaction_id, None)
            return final_message
        except Exception as e:
            # Clean up orphaned accumulator on error
            if interaction_id:
                self._adhoc_accumulation.pop(interaction_id, None)
            raise

    async def commit_pending_adhoc(
        self,
        interaction_id: str,
        interaction: Any,
    ) -> None:
        """Commit any pending streaming adhoc content for this interaction.

        Ensures all accumulated content is finalized (filters/adapters triggered),
        written to interaction.response, and saved. Called by walker between action transitions.
        """
        if interaction_id not in self._adhoc_accumulation:
            return
        acc = self._adhoc_accumulation[interaction_id]
        if not acc.chunks:
            self._adhoc_accumulation.pop(interaction_id, None)
            return
        full_content = "".join(acc.chunks)
        now = await self._get_now()
        message = ResponseMessage(
            session_id=acc.session_id,
            user_id=acc.user_id or "",
            interaction_id=interaction_id,
            content=full_content,
            channel=acc.channel,
            message_type="adhoc",
            metadata=acc.metadata or {},
            timestamp=now,
        )
        filter_ok = await self._apply_channel_filters(message, acc.channel)
        if filter_ok:
            if acc.channel in self._channel_adapters:
                await self._send_to_adapter(
                    self._channel_adapters[acc.channel], message
                )
            if full_content and interaction:
                await self._append_to_interaction_response_impl(
                    interaction=interaction,
                    message_type="adhoc",
                    content=message.content,
                )
        self._adhoc_accumulation.pop(interaction_id, None)

    async def _emit_final_signal(
        self,
        session_id: str,
        channel: str,
        interaction_id: str,
        user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        message_id: Optional[str] = None,
    ) -> None:
        """Internal: enqueue a final ResponseMessage and notify subscribers (no filters/adapters)."""
        now = await self._get_now()
        final_message = ResponseMessage(
            id=message_id or f"o.ResponseMessage.{uuid.uuid4().hex[:24]}",
            session_id=session_id,
            user_id=user_id or "",
            interaction_id=interaction_id,
            content="",
            channel=channel,
            message_type="final",
            metadata=metadata or {},
            timestamp=now,
        )
        await self._enqueue_and_notify(final_message, session_id)

    async def _safe_awaitable(self, awaitable: Any) -> None:
        """Safely await a coroutine/awaitable with error handling."""
        try:
            await awaitable
        except Exception as e:
            logger.error(
                f"Error in subscriber callback: {e}",
                exc_info=True,
            )

    async def _append_to_interaction_response_impl(
        self,
        interaction: Any,
        message_type: str,
        content: str,
    ) -> None:
        """Append published content to an interaction instance (no DB lookup).

        Args:
            interaction: Interaction instance to update
            message_type: "adhoc" or "stream_chunk"
            content: Content to append (already filtered)
        """
        current_response = interaction.response or ""
        if message_type == "adhoc":
            if current_response:
                new_response = f"{current_response}\n\n{content}"
            else:
                new_response = content
        elif message_type == "stream_chunk":
            new_response = f"{current_response}{content}"
        else:
            return

        if interaction.set_response(new_response):
            if (
                not hasattr(interaction, "_graph_context")
                or interaction._graph_context is None
            ):
                try:
                    from jvspatial.core.context import get_default_context

                    interaction._graph_context = get_default_context()
                except Exception:
                    pass
            await interaction.save()

    async def _send_to_adapter(
        self, adapter: "ChannelAdapter", message: ResponseMessage
    ) -> bool:
        """Send message to adapter with lightweight retry (max 2 attempts).

        Returns:
            True if sent successfully, False on permanent failure or exhausted retries.
        """
        for attempt in range(2):
            try:
                if await adapter.send(message):
                    return True
                return False  # Permanent failure (e.g., invalid recipient)
            except Exception as e:
                if attempt == 0:
                    await asyncio.sleep(0.5)
                    logger.warning(f"Adapter retry for {adapter.channel}: {e}")
                else:
                    logger.error(
                        f"Adapter failed for {adapter.channel}: {e}", exc_info=True
                    )
        return False

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
            self._subscriber_ids[session_id] = set()

        # Use O(1) set lookup for duplicate check instead of O(n) list search
        cb_id = id(callback)
        if cb_id not in self._subscriber_ids[session_id]:
            self._subscribers[session_id].append(callback)
            self._subscriber_ids[session_id].add(cb_id)

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
                # Also remove from the O(1) lookup set
                cb_id = id(callback)
                if session_id in self._subscriber_ids:
                    self._subscriber_ids[session_id].discard(cb_id)
            except ValueError:
                pass  # Callback not in list

        # Clean up preferences
        if callback in self._subscriber_preferences:
            del self._subscriber_preferences[callback]

    async def finalize_interaction(
        self,
        interaction_id: str,
        interaction: Any,
        session_id: str,
        channel: str = "default",
    ) -> None:
        """Finalize an interaction. Commits pending streaming content, emits end-of-cycle signal, cleans buffers.

        Args:
            interaction_id: Interaction ID to finalize
            interaction: Interaction node instance to update
            session_id: Session ID for publishing final message
            channel: Channel for final message
        """
        # Commit any pending streaming content
        await self.commit_pending_adhoc(interaction_id, interaction)

        # Emit final signal
        user_id = getattr(interaction, "user_id", None) if interaction else None
        await self._emit_final_signal(
            session_id=session_id,
            channel=channel,
            interaction_id=interaction_id,
            user_id=user_id,
            metadata={},
        )

        # Clean up request-scoped resources (adhoc, message buffers)
        self._adhoc_accumulation.pop(interaction_id, None)
        self._message_buffers.pop(interaction_id, None)

        # Clean old session messages
        await self._cleanup_old_session_messages(session_id)

        # Trigger lazy cleanup (throttled)
        self._maybe_cleanup()

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
        # Clean up subscriber ID set
        if session_id in self._subscriber_ids:
            del self._subscriber_ids[session_id]

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
        now = await self._get_now()
        cutoff_dt = now - timedelta(minutes=5)

        # ResponseMessage.timestamp is timezone-aware datetime; keep any messages newer than cutoff.
        filtered_queue = [
            msg for msg in queue if msg.timestamp and msg.timestamp > cutoff_dt
        ]

        if len(filtered_queue) < len(queue):
            self._session_queues[session_id] = filtered_queue
            logger.debug(
                f"Cleaned up {len(queue) - len(filtered_queue)} old messages from session {session_id}"
            )

    async def cleanup_expired_buffers(self) -> None:
        """Clean up expired accumulation and observability buffers (TTL cleanup).

        This should be called periodically to prevent memory leaks from interactions
        that never finalize (e.g., due to crashes or cancellations).
        """
        current_time = time.time()
        expired_interaction_ids = [
            interaction_id
            for interaction_id, timestamp in self._buffer_timestamps.items()
            if current_time - timestamp > self.BUFFER_TTL_SECONDS
        ]

        for interaction_id in expired_interaction_ids:
            logger.debug(
                f"Cleaning up expired buffers for interaction {interaction_id}"
            )
            self._message_buffers.pop(interaction_id, None)
            self._adhoc_accumulation.pop(interaction_id, None)
            self._buffer_timestamps.pop(interaction_id, None)

    async def register_channel_adapter(self, adapter: "ChannelAdapter") -> None:
        """Register a channel adapter with the response bus.

        Ensures only ONE adapter per channel. If an adapter already exists for the channel,
        it is replaced by the new adapter. This prevents duplicate message delivery.

        Args:
            adapter: ChannelAdapter instance to register
        """
        if not hasattr(adapter, "channel"):
            logger.warning(
                f"Adapter {adapter} does not have a channel attribute, skipping registration"
            )
            return

        channel = adapter.channel
        old_adapter = self._channel_adapters.get(channel)

        # Replace any existing adapter for this channel (ensures only one adapter per channel)
        self._channel_adapters[channel] = adapter

        if old_adapter and old_adapter is not adapter:
            logger.info(f"Replaced existing channel adapter for channel '{channel}'")
        else:
            logger.debug(f"Registered channel adapter for channel '{channel}'")

    async def register_channel_filter(self, filter: "ChannelFilter") -> None:
        """Register a channel filter with the response bus.

        Filters are stored in priority order (lower priority executes first).
        Multiple filters can be registered for the same channel.

        Args:
            filter: ChannelFilter instance to register
        """
        if not hasattr(filter, "channels") or not hasattr(filter, "priority"):
            logger.warning(
                f"Filter {filter} does not have required attributes (channels, priority), skipping registration"
            )
            return

        # Add filter to list
        self._channel_filters.append(filter)

        # Sort filters by priority (lower priority executes first)
        self._channel_filters.sort(key=lambda f: f.priority)

        channel_list = ", ".join(filter.channels)
        logger.debug(
            f"Registered channel filter for channels [{channel_list}] "
            f"(priority: {filter.priority}, total filters: {len(self._channel_filters)})"
        )

    async def _apply_channel_filters(
        self, message: ResponseMessage, channel: str
    ) -> bool:
        """Apply all registered filters for a channel to the message.

        Filters execute in priority order (lower priority first).
        Each filter transforms the message in-place.

        Args:
            message: ResponseMessage to transform
            channel: Channel name to filter for

        Returns:
            False if a fail_fast filter fails, True otherwise
        """
        # Get all filters that apply to this channel, sorted by priority
        applicable_filters = [
            f for f in self._channel_filters if f.applies_to_channel(channel)
        ]

        if not applicable_filters:
            return True

        # Apply each filter in priority order
        for filter_instance in applicable_filters:
            try:
                await filter_instance.filter(message)
            except Exception as e:
                logger.error(
                    f"Error applying channel filter {filter_instance.__class__.__name__} "
                    f"for channel '{channel}': {e}",
                    exc_info=True,
                )
                if getattr(filter_instance, "fail_fast", False):
                    return False

        return True
