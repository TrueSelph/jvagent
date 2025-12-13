"""Interaction event bus and event types for PersonaAction.

This module provides the event system for asynchronous interaction processing:
- InteractionEventType: Enum of all event types
- InteractionEvent: Event data container
- InteractionEventBus: Pub/sub event bus for interactions
- ResponseAggregator: Collects events into final interaction result
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class InteractionEventType(str, Enum):
    """Types of events emitted during interaction processing."""

    # Lifecycle events
    INTERACTION_STARTED = "interaction_started"
    INTERACTION_COMPLETE = "interaction_complete"

    # Response events
    CANNED_RESPONSE = "canned_response"  # Immediate response before processing
    RESPONSE_CHUNK = "response_chunk"  # Streaming chunk
    RESPONSE_COMPLETE = "response_complete"  # Final response

    # Parameter events
    PARAMETER_FILTERED = "parameter_filtered"  # Parameters selected by LLM
    PARAMETER_APPLIED = "parameter_applied"  # Parameter applied to prompt

    # Action events
    ACTION_TRIGGERED = "action_triggered"  # Action execution started
    ACTION_RESULT = "action_result"  # Directive returned by action

    # Other events
    DIRECTIVE_ADDED = "directive_added"
    LOG = "log"
    ERROR = "error"


@dataclass
class InteractionEvent:
    """Event data container for interaction events.

    Attributes:
        event_type: Type of the event
        interaction_id: ID of the interaction this event belongs to
        timestamp: When the event occurred
        data: Event-specific data payload
    """

    event_type: InteractionEventType
    interaction_id: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert event to dictionary for serialization.

        Returns:
            Dictionary representation of the event
        """
        return {
            "event_type": self.event_type.value,
            "interaction_id": self.interaction_id,
            "timestamp": self.timestamp.isoformat(),
            "data": self.data,
        }


# Type alias for event callbacks
EventCallback = Callable[[InteractionEvent], Awaitable[None]]


class InteractionEventBus:
    """Event bus for asynchronous interaction processing.

    Provides pub/sub functionality for interaction events, enabling:
    - Canned responses to be emitted immediately before processing
    - Streaming chunks to be emitted as they arrive
    - Logs/errors to be tracked throughout processing
    - Subscribers to aggregate the final interaction

    Attributes:
        interaction_id: ID of the interaction this bus belongs to
    """

    def __init__(self, interaction_id: str):
        """Initialize the event bus.

        Args:
            interaction_id: ID of the interaction this bus belongs to
        """
        self.interaction_id = interaction_id
        self._subscribers: Dict[InteractionEventType, List[EventCallback]] = {}
        self._all_subscribers: List[EventCallback] = []
        self._events: List[InteractionEvent] = []
        self._lock = asyncio.Lock()

    def subscribe(
        self, event_type: InteractionEventType, callback: EventCallback
    ) -> None:
        """Subscribe to a specific event type.

        Args:
            event_type: Type of event to subscribe to
            callback: Async callback to invoke when event is emitted
        """
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(callback)

    def subscribe_all(self, callback: EventCallback) -> None:
        """Subscribe to all event types.

        Args:
            callback: Async callback to invoke for all events
        """
        self._all_subscribers.append(callback)

    def unsubscribe(
        self, event_type: InteractionEventType, callback: EventCallback
    ) -> None:
        """Unsubscribe from a specific event type.

        Args:
            event_type: Type of event to unsubscribe from
            callback: Callback to remove
        """
        if event_type in self._subscribers:
            self._subscribers[event_type] = [
                cb for cb in self._subscribers[event_type] if cb != callback
            ]

    async def emit(self, event: InteractionEvent) -> None:
        """Emit an event to all subscribers.

        Args:
            event: Event to emit
        """
        async with self._lock:
            self._events.append(event)

        # Notify type-specific subscribers
        if event.event_type in self._subscribers:
            for callback in self._subscribers[event.event_type]:
                try:
                    await callback(event)
                except Exception as e:
                    logger.error(f"Error in event callback: {e}")

        # Notify all-event subscribers
        for callback in self._all_subscribers:
            try:
                await callback(event)
            except Exception as e:
                logger.error(f"Error in all-event callback: {e}")

    async def emit_interaction_started(self, **data: Any) -> None:
        """Emit an interaction started event.

        Args:
            **data: Additional event data
        """
        await self.emit(
            InteractionEvent(
                event_type=InteractionEventType.INTERACTION_STARTED,
                interaction_id=self.interaction_id,
                data=data,
            )
        )

    async def emit_canned_response(
        self, content: str, category: str = ""
    ) -> None:
        """Emit a canned response event.

        Args:
            content: Canned response content
            category: Response category (e.g., 'greeting', 'complex_request')
        """
        await self.emit(
            InteractionEvent(
                event_type=InteractionEventType.CANNED_RESPONSE,
                interaction_id=self.interaction_id,
                data={"content": content, "category": category},
            )
        )

    async def emit_response_chunk(self, chunk: str, index: int = 0) -> None:
        """Emit a response chunk for streaming.

        Args:
            chunk: Response chunk content
            index: Chunk index for ordering
        """
        await self.emit(
            InteractionEvent(
                event_type=InteractionEventType.RESPONSE_CHUNK,
                interaction_id=self.interaction_id,
                data={"chunk": chunk, "index": index},
            )
        )

    async def emit_response_complete(
        self, content: str, metrics: Optional[Dict[str, Any]] = None
    ) -> None:
        """Emit a response complete event.

        Args:
            content: Complete response content
            metrics: Optional metrics dictionary
        """
        await self.emit(
            InteractionEvent(
                event_type=InteractionEventType.RESPONSE_COMPLETE,
                interaction_id=self.interaction_id,
                data={"content": content, "metrics": metrics or {}},
            )
        )

    async def emit_parameter_filtered(
        self, parameters: List[Dict[str, Any]]
    ) -> None:
        """Emit a parameter filtered event.

        Args:
            parameters: List of filtered parameter dictionaries
        """
        await self.emit(
            InteractionEvent(
                event_type=InteractionEventType.PARAMETER_FILTERED,
                interaction_id=self.interaction_id,
                data={"parameters": parameters},
            )
        )

    async def emit_parameter_applied(
        self, parameter_id: str, condition: str, response: str
    ) -> None:
        """Emit a parameter applied event.

        Args:
            parameter_id: ID of the applied parameter
            condition: Parameter condition
            response: Parameter response instruction
        """
        await self.emit(
            InteractionEvent(
                event_type=InteractionEventType.PARAMETER_APPLIED,
                interaction_id=self.interaction_id,
                data={
                    "parameter_id": parameter_id,
                    "condition": condition,
                    "response": response,
                },
            )
        )

    async def emit_action_triggered(self, action_label: str) -> None:
        """Emit an action triggered event.

        Args:
            action_label: Label of the triggered action
        """
        await self.emit(
            InteractionEvent(
                event_type=InteractionEventType.ACTION_TRIGGERED,
                interaction_id=self.interaction_id,
                data={"action_label": action_label},
            )
        )

    async def emit_action_result(
        self, action_label: str, directive: str
    ) -> None:
        """Emit an action result event.

        Args:
            action_label: Label of the action
            directive: Directive returned by the action
        """
        await self.emit(
            InteractionEvent(
                event_type=InteractionEventType.ACTION_RESULT,
                interaction_id=self.interaction_id,
                data={"action_label": action_label, "directive": directive},
            )
        )

    async def emit_directive_added(self, directive: str) -> None:
        """Emit a directive added event.

        Args:
            directive: Directive that was added
        """
        await self.emit(
            InteractionEvent(
                event_type=InteractionEventType.DIRECTIVE_ADDED,
                interaction_id=self.interaction_id,
                data={"directive": directive},
            )
        )

    async def emit_log(
        self, level: str, message: str, **data: Any
    ) -> None:
        """Emit a log event.

        Args:
            level: Log level (debug, info, warning, error)
            message: Log message
            **data: Additional log data
        """
        await self.emit(
            InteractionEvent(
                event_type=InteractionEventType.LOG,
                interaction_id=self.interaction_id,
                data={"level": level, "message": message, **data},
            )
        )

    async def emit_error(self, error: str, **data: Any) -> None:
        """Emit an error event.

        Args:
            error: Error message
            **data: Additional error data
        """
        await self.emit(
            InteractionEvent(
                event_type=InteractionEventType.ERROR,
                interaction_id=self.interaction_id,
                data={"error": error, **data},
            )
        )

    async def emit_interaction_complete(
        self, metrics: Optional[Dict[str, Any]] = None
    ) -> None:
        """Emit an interaction complete event.

        Args:
            metrics: Optional final metrics
        """
        await self.emit(
            InteractionEvent(
                event_type=InteractionEventType.INTERACTION_COMPLETE,
                interaction_id=self.interaction_id,
                data={"metrics": metrics or {}},
            )
        )

    def get_events(self) -> List[InteractionEvent]:
        """Get all emitted events.

        Returns:
            List of all events in order of emission
        """
        return list(self._events)

    def get_events_by_type(
        self, event_type: InteractionEventType
    ) -> List[InteractionEvent]:
        """Get events of a specific type.

        Args:
            event_type: Type of events to retrieve

        Returns:
            List of events matching the type
        """
        return [e for e in self._events if e.event_type == event_type]


class ResponseAggregator:
    """Aggregates events into final interaction result.

    Handles both streaming and synchronous responses by collecting events
    from the event bus and populating the interaction with the results.

    Note: Parameters, actions, and metrics are tracked directly on the
    Interaction (parameters via add_parameter(), actions via add_action(),
    metrics via model_log). The aggregator focuses on responses, directives,
    and logs.

    Attributes:
        event_bus: The event bus to aggregate from
        canned_response: Captured canned response (if any)
        response_chunks: List of streaming chunks
        final_response: Complete response (from RESPONSE_COMPLETE)
        directives: List of collected directives
        logs: List of log entries
        errors: List of error messages
    """

    def __init__(self, event_bus: InteractionEventBus):
        """Initialize the aggregator.

        Args:
            event_bus: Event bus to aggregate from
        """
        self.event_bus = event_bus
        self.canned_response: Optional[str] = None
        self.response_chunks: List[str] = []
        self.final_response: Optional[str] = None
        self.directives: List[str] = []
        self.logs: List[Dict[str, Any]] = []
        self.errors: List[str] = []

        self._setup_subscriptions()

    def _setup_subscriptions(self) -> None:
        """Subscribe to relevant events for aggregation.

        Note: Parameters and actions are tracked directly on Interaction,
        so we only subscribe to response, directive, log, and error events.
        """
        self.event_bus.subscribe(
            InteractionEventType.CANNED_RESPONSE, self._on_canned_response
        )
        self.event_bus.subscribe(
            InteractionEventType.RESPONSE_CHUNK, self._on_response_chunk
        )
        self.event_bus.subscribe(
            InteractionEventType.RESPONSE_COMPLETE, self._on_response_complete
        )
        self.event_bus.subscribe(
            InteractionEventType.ACTION_RESULT, self._on_action_result
        )
        self.event_bus.subscribe(
            InteractionEventType.DIRECTIVE_ADDED, self._on_directive_added
        )
        self.event_bus.subscribe(InteractionEventType.LOG, self._on_log)
        self.event_bus.subscribe(InteractionEventType.ERROR, self._on_error)
        self.event_bus.subscribe(
            InteractionEventType.INTERACTION_COMPLETE, self._on_interaction_complete
        )

    async def _on_canned_response(self, event: InteractionEvent) -> None:
        """Handle canned response event."""
        self.canned_response = event.data.get("content")

    async def _on_response_chunk(self, event: InteractionEvent) -> None:
        """Handle response chunk event."""
        chunk = event.data.get("chunk", "")
        if chunk:
            self.response_chunks.append(chunk)

    async def _on_response_complete(self, event: InteractionEvent) -> None:
        """Handle response complete event."""
        self.final_response = event.data.get("content")

    async def _on_action_result(self, event: InteractionEvent) -> None:
        """Handle action result event."""
        directive = event.data.get("directive")
        if directive and directive not in self.directives:
            self.directives.append(directive)

    async def _on_directive_added(self, event: InteractionEvent) -> None:
        """Handle directive added event."""
        directive = event.data.get("directive")
        if directive and directive not in self.directives:
            self.directives.append(directive)

    async def _on_log(self, event: InteractionEvent) -> None:
        """Handle log event."""
        self.logs.append(event.data)

    async def _on_error(self, event: InteractionEvent) -> None:
        """Handle error event."""
        error = event.data.get("error")
        if error:
            self.errors.append(error)

    async def _on_interaction_complete(self, event: InteractionEvent) -> None:
        """Handle interaction complete event."""
        pass  # Metrics are captured in model_log on the Interaction

    def get_full_response(self) -> str:
        """Get the full response, combining chunks if needed.

        Returns:
            Complete response string
        """
        if self.final_response:
            return self.final_response
        if self.response_chunks:
            return "".join(self.response_chunks)
        return ""

    def populate_interaction(self, interaction: Any, action_label: str = "PersonaAction") -> Any:
        """Populate an Interaction node with aggregated data.

        Note: Parameters and actions are tracked directly on Interaction
        via add_parameter() and add_action() during processing. Metrics
        are captured in model_log. This method only handles responses
        and directives from events.

        Args:
            interaction: Interaction node to populate
            action_label: Class name of the action populating the interaction (default: "PersonaAction")

        Returns:
            The populated Interaction node
        """
        # Set responses
        if self.canned_response:
            interaction.canned_response = self.canned_response
        if self.final_response or self.response_chunks:
            interaction.response = self.get_full_response()

        # Add directives from events
        for directive in self.directives:
            interaction.add_directive(directive, action_label)

        return interaction

    def to_dict(self) -> Dict[str, Any]:
        """Convert aggregator state to dictionary.

        Returns:
            Dictionary representation of aggregated state
        """
        return {
            "canned_response": self.canned_response,
            "response": self.get_full_response(),
            "directives": self.directives,
            "logs": self.logs,
            "errors": self.errors,
        }
