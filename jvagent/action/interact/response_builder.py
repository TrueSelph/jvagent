"""Response builder for interact endpoint with production filtering."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from jvagent.core.config import is_production_mode
from jvagent.memory.interaction import Interaction

_TERMINAL_COMPLETED_STATUSES = {"completed"}


def _parse_interaction_timestamp(value: Any) -> Optional[datetime]:
    """Parse datetime-like values from interaction/task payloads."""
    if value is None:
        return None

    dt: Optional[datetime] = None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None

    if dt and dt.tzinfo is None:
        from datetime import timezone

        dt = dt.replace(tzinfo=timezone.utc)

    return dt


def _completed_tasks_for_interaction(
    interaction: Interaction,
    conversation: Any,
) -> List[Dict[str, Any]]:
    """Return tasks completed during this interaction window."""
    started_at = _parse_interaction_timestamp(getattr(interaction, "started_at", None))
    completed_at = _parse_interaction_timestamp(
        getattr(interaction, "completed_at", None)
    )
    if not started_at:
        return []

    completed: List[Dict[str, Any]] = []
    for task in getattr(conversation, "active_tasks", []):
        if task.get("status") not in _TERMINAL_COMPLETED_STATUSES:
            continue
        updated_at = _parse_interaction_timestamp(task.get("updated_at"))
        if not updated_at:
            continue
        if updated_at < started_at:
            continue
        if completed_at and updated_at > completed_at:
            continue
        completed.append(task)
    return completed


def build_interaction_payload(
    interaction: Interaction,
    active_tasks: Optional[List[Dict[str, Any]]] = None,
    completed_tasks: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build interaction payload, filtering debug data in production.

    In production mode (JVSPATIAL_ENVIRONMENT=production), returns
    minimal payload with only: id, utterance, response.

    In development mode, returns full payload with: id, utterance, response,
    actions, directives, parameters, events, active_tasks, observability_metrics, streamed.

    Args:
        interaction: Interaction node instance
        active_tasks: Optional list of active tasks from conversation (dev mode only)

    Returns:
        Dictionary with interaction data (filtered based on environment)
    """
    if is_production_mode():
        # Minimal production payload - only essential fields
        return {
            "id": interaction.id,
            "utterance": interaction.utterance,
            "response": interaction.response,
        }
    else:
        # Full development payload - includes all debug/observability data
        payload = {
            "id": interaction.id,
            "utterance": interaction.utterance,
            "response": interaction.response,
            "actions": interaction.actions,
            "directives": interaction.directives,
            "active_tasks": active_tasks if active_tasks is not None else [],
            "completed_tasks": completed_tasks if completed_tasks is not None else [],
            "parameters": interaction.parameters,
            "events": interaction.events,
            "observability_metrics": interaction.observability_metrics,
            "usage": getattr(interaction, "usage", None) or {},
            "streamed": interaction.streamed,
        }

        return payload


async def build_interact_response(
    user_id: str,
    session_id: str,
    interaction: Interaction,
    report: Optional[list] = None,
) -> Dict[str, Any]:
    """Build complete interact endpoint response with environment-based filtering.

    Always includes:
    - user_id, session_id, response

    Only in development mode:
    - interaction (full payload with id, utterance, response, actions, etc.)
    - report (walker traversal report)

    Args:
        user_id: User identifier
        session_id: Session identifier
        interaction: Interaction node instance
        report: Optional walker report (only included in development)

    Returns:
        Dictionary with complete response payload
    """
    response: Dict[str, Any] = {
        "user_id": user_id,
        "session_id": session_id,
        "response": interaction.response,
    }
    if not is_production_mode():
        active_tasks: List[Dict[str, Any]] = []
        if interaction.conversation_id:
            from jvagent.memory.conversation import Conversation

            conversation = await Conversation.get(interaction.conversation_id)
            if conversation:
                active_tasks = conversation.get_active_tasks(status="active")
                completed_tasks = _completed_tasks_for_interaction(
                    interaction, conversation
                )
            else:
                completed_tasks = []
        else:
            completed_tasks = []
        response["interaction"] = build_interaction_payload(
            interaction,
            active_tasks=active_tasks,
            completed_tasks=completed_tasks,
        )

    # Include report only in development mode
    # In production mode, omit the field entirely (not set to None)
    if not is_production_mode() and report is not None:
        response["report"] = report

    return response
