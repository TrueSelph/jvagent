"""Events for Initial Phase Action.

This module provides event bus and event types for Initial Phase processing.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class InitialPhaseEvent:
    """Base event for Initial Phase processing.

    Attributes:
        event_type: Type of event
        interaction_id: Associated interaction ID
        timestamp: Event timestamp
        data: Event data payload
    """

    event_type: str
    interaction_id: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "event_type": self.event_type,
            "interaction_id": self.interaction_id,
            "timestamp": self.timestamp.isoformat(),
            "data": self.data,
        }


class InitialPhaseEventBus:
    """Event bus for Initial Phase processing.

    Collects and manages events during Initial Phase processing,
    providing an event log for debugging and monitoring.
    """

    def __init__(self, interaction_id: str):
        """Initialize event bus.

        Args:
            interaction_id: Associated interaction ID
        """
        self.interaction_id = interaction_id
        self._events: List[InitialPhaseEvent] = []

    async def emit(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> None:
        """Emit an event.

        Args:
            event_type: Type of event
            data: Event data payload
        """
        event = InitialPhaseEvent(
            event_type=event_type,
            interaction_id=self.interaction_id,
            data=data or {},
        )
        self._events.append(event)

    async def emit_phase_started(self, utterance: str, user_id: str, session_id: str) -> None:
        """Emit phase started event."""
        await self.emit("phase_started", {
            "utterance": utterance,
            "user_id": user_id,
            "session_id": session_id,
        })

    async def emit_vector_search_started(self, query: str) -> None:
        """Emit vector search started event."""
        await self.emit("vector_search_started", {"query": query})

    async def emit_vector_search_complete(
        self,
        parameters_found: int,
        competencies_found: int,
        search_duration: float,
    ) -> None:
        """Emit vector search complete event."""
        await self.emit("vector_search_complete", {
            "parameters_found": parameters_found,
            "competencies_found": competencies_found,
            "search_duration_ms": search_duration * 1000,
        })

    async def emit_parameters_filtered(self, parameter_ids: List[str]) -> None:
        """Emit parameters filtered event."""
        await self.emit("parameters_filtered", {
            "parameter_ids": parameter_ids,
            "count": len(parameter_ids),
        })

    async def emit_competencies_filtered(self, competency_ids: List[str]) -> None:
        """Emit competencies filtered event."""
        await self.emit("competencies_filtered", {
            "competency_ids": competency_ids,
            "count": len(competency_ids),
        })

    async def emit_llm_evaluation_started(self) -> None:
        """Emit LLM evaluation started event."""
        await self.emit("llm_evaluation_started", {})

    async def emit_llm_evaluation_complete(
        self,
        simplified_intent: str,
        parameters_count: int,
        actions_count: int,
        workflows_count: int,
        evaluation_duration: float,
    ) -> None:
        """Emit LLM evaluation complete event."""
        await self.emit("llm_evaluation_complete", {
            "simplified_intent": simplified_intent,
            "parameters_count": parameters_count,
            "actions_count": actions_count,
            "workflows_count": workflows_count,
            "evaluation_duration_ms": evaluation_duration * 1000,
        })

    async def emit_instructions_generated(self, instructions: Dict[str, Any]) -> None:
        """Emit instructions generated event."""
        await self.emit("instructions_generated", {
            "instructions_summary": {
                "intent": instructions.get("simplified_intent"),
                "parameters": len(instructions.get("applicable_parameters", [])),
                "actions": len(instructions.get("required_actions", [])),
                "workflows": len(instructions.get("required_workflows", [])),
            }
        })

    async def emit_phase_complete(self, total_duration: float) -> None:
        """Emit phase complete event."""
        await self.emit("phase_complete", {
            "total_duration_ms": total_duration * 1000,
        })

    async def emit_error(self, error_message: str, error_details: Optional[Dict[str, Any]] = None) -> None:
        """Emit error event."""
        await self.emit("error", {
            "message": error_message,
            "details": error_details or {},
        })

    async def emit_log(self, level: str, message: str, data: Optional[Dict[str, Any]] = None) -> None:
        """Emit log event."""
        await self.emit("log", {
            "level": level,
            "message": message,
            "data": data or {},
        })

    def get_events(self) -> List[InitialPhaseEvent]:
        """Get all events.

        Returns:
            List of events
        """
        return self._events

    def get_events_by_type(self, event_type: str) -> List[InitialPhaseEvent]:
        """Get events of a specific type.

        Args:
            event_type: Event type to filter by

        Returns:
            List of events matching type
        """
        return [e for e in self._events if e.event_type == event_type]

    def to_dict(self) -> Dict[str, Any]:
        """Convert event bus to dictionary.

        Returns:
            Dictionary with all events
        """
        return {
            "interaction_id": self.interaction_id,
            "events": [e.to_dict() for e in self._events],
            "event_count": len(self._events),
        }

    def to_json(self) -> str:
        """Convert event bus to JSON string.

        Returns:
            JSON string with all events
        """
        return json.dumps(self.to_dict(), indent=2)
