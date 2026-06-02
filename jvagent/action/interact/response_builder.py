"""Response builder for interact endpoint with production filtering."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from jvagent.core.config import is_production_mode
from jvagent.memory.interaction import Interaction

_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


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


def _terminal_tasks_for_interaction(
    interaction: Interaction,
    conversation: Any,
) -> List[Dict[str, Any]]:
    """Return tasks reaching any terminal status during this interaction window.

    Terminal statuses: ``completed``, ``failed``, ``cancelled``.
    """
    started_at = _parse_interaction_timestamp(getattr(interaction, "started_at", None))
    completed_at = _parse_interaction_timestamp(
        getattr(interaction, "completed_at", None)
    )
    if not started_at:
        return []

    out: List[Dict[str, Any]] = []
    for task in getattr(conversation, "tasks", []):
        if task.get("status") not in _TERMINAL_STATUSES:
            continue
        updated_at = _parse_interaction_timestamp(task.get("updated_at"))
        if not updated_at:
            continue
        if updated_at < started_at:
            continue
        if completed_at and updated_at > completed_at:
            continue
        out.append(task)
    return out


def _consolidated_tasks_for_interaction(
    interaction: Interaction,
    conversation: Any,
    active_tasks: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build the consolidated ``tasks`` array with status on each entry.

    Includes:
    - Currently active tasks on the conversation.
    - Tasks reaching any terminal status (completed, failed, cancelled) within
      this interaction's window.

    Deduplicated by ``id`` (active wins on overlap), ordered by ``updated_at``
    ascending so consumers see chronological progression.
    """
    seen: Dict[str, Dict[str, Any]] = {}
    for t in active_tasks or []:
        tid = t.get("id")
        if tid:
            seen[tid] = t
    for t in _terminal_tasks_for_interaction(interaction, conversation):
        tid = t.get("id")
        if tid and tid not in seen:
            seen[tid] = t

    def _sort_key(t: Dict[str, Any]) -> Any:
        ts = _parse_interaction_timestamp(
            t.get("updated_at")
        ) or _parse_interaction_timestamp(t.get("created_at"))
        return ts or datetime.min

    return sorted(seen.values(), key=_sort_key)


def build_interaction_payload(
    interaction: Interaction,
    tasks: Optional[List[Dict[str, Any]]] = None,
    *,
    redact_debug: bool = False,
) -> Dict[str, Any]:
    """Build interaction payload, filtering debug data in production.

    In production mode (JVSPATIAL_ENVIRONMENT=production), returns
    minimal payload with only: id, utterance, response.

    In development mode, returns full payload including a consolidated
    ``tasks`` array. Each entry carries ``status`` (``active``,
    ``completed``, ``failed``, ``cancelled``) so consumers differentiate
    by inspecting the task itself rather than reading separate arrays.

    Args:
        interaction: Interaction node instance
        tasks: Consolidated tasks list (active + terminal in this window)

    Returns:
        Dictionary with interaction data (filtered based on environment)
    """
    if is_production_mode() or redact_debug:
        # Minimal production payload - only essential fields
        return {
            "id": interaction.id,
            "utterance": interaction.utterance,
            "response": interaction.response,
        }
    return {
        "id": interaction.id,
        "utterance": interaction.utterance,
        "response": interaction.response,
        "actions": interaction.actions,
        "directives": interaction.directives,
        # Unified canonical tasks list — each entry carries its own status.
        "tasks": tasks if tasks is not None else [],
        "parameters": interaction.parameters,
        "events": interaction.events,
        "observability_metrics": interaction.observability_metrics,
        "usage": getattr(interaction, "usage", None) or {},
        "streamed": interaction.streamed,
    }


async def build_interact_response(
    user_id: str,
    session_id: str,
    interaction: Interaction,
    report: Optional[list] = None,
    *,
    public_endpoint: bool = False,
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
    redact = is_production_mode() or public_endpoint
    if not redact:
        tasks: List[Dict[str, Any]] = []
        if interaction.conversation_id:
            from jvagent.memory.conversation import Conversation

            conversation = await Conversation.get(interaction.conversation_id)
            if conversation:
                active_tasks = conversation.get_tasks(status="active")
                tasks = _consolidated_tasks_for_interaction(
                    interaction, conversation, active_tasks
                )
        response["interaction"] = build_interaction_payload(
            interaction, tasks=tasks, redact_debug=public_endpoint
        )

    if not redact and report is not None:
        response["report"] = report

    return response
