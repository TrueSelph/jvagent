"""Response builder for interact endpoint with production filtering."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from jvagent.core.config import is_production_mode
from jvagent.memory.interaction import Interaction


def _public_debug_hardened() -> bool:
    """True when the public endpoint should redact debug outside production.

    Off by default so local dev (the jvchat Debug view) keeps full detail; set
    ``JVAGENT_INTERACT_REDACT_DEBUG`` truthy to harden a non-prod internet deploy
    (production always redacts via ``is_production_mode``).
    """
    import os

    return os.environ.get("JVAGENT_INTERACT_REDACT_DEBUG", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


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
    """Build the consolidated ``tasks`` array — the FULL work graph for inspection.

    Includes every task on the conversation, each carrying its ``status``
    (``active``/``pending``/``completed``/``failed``/``cancelled``), its
    ``blocked_on``/``resumes`` edges, and a derived ``blocked`` flag (non-terminal
    with an unmet prerequisite — "blocked" is not a status, so it is computed here).
    Deduplicated by ``id``, ordered by ``updated_at`` ascending so consumers see the
    chronological progression. Terminal tasks are pruned from the store by
    ``sweep_terminal``; until then the whole graph is visible.
    """
    seen: Dict[str, Dict[str, Any]] = {}
    # The full graph (every status), so the debug surface is complete rather than
    # windowed to this turn. Fall back to the passed-in active set if the full
    # read fails for any reason.
    try:
        full = conversation.get_tasks()
    except Exception:
        full = list(active_tasks or [])
    for t in full:
        tid = t.get("id")
        if tid:
            seen[tid] = t
    for t in active_tasks or []:  # defensive: ensure the active set is present
        tid = t.get("id")
        if tid and tid not in seen:
            seen[tid] = t

    # Derive `blocked`: non-terminal with a prerequisite that is not completed.
    status_by_id = {t.get("id"): t.get("status") for t in seen.values()}
    for t in seen.values():
        blockers = t.get("blocked_on") or []
        t["blocked"] = bool(
            t.get("status") in ("pending", "active")
            and any(status_by_id.get(b) != "completed" for b in blockers)
        )

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
    # Debug/observability redaction. Production always redacts. The public
    # endpoint (auth=False) additionally redacts only when explicitly hardened
    # (``JVAGENT_INTERACT_REDACT_DEBUG``) so anonymous callers on a non-prod
    # *internet* deploy don't leak internals — but local dev (the jvchat Debug
    # view's audience) keeps full detail by default.
    redact = is_production_mode() or (public_endpoint and _public_debug_hardened())
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
            interaction, tasks=tasks, redact_debug=False
        )

    if not redact and report is not None:
        response["report"] = report

    return response
