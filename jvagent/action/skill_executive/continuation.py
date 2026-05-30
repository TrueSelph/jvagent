"""Active-flow awareness for the SkillExecutive (ADR-0012).

Turn-spanning flows (e.g. the signup interview) record an active control-task on
the conversation ``TaskStore`` while running. The SkillExecutive does **not**
force every turn back into the flow when ``lock_active_flow`` is off — that
would shove off-topic utterances into an active interview. Instead it surfaces
the active flow to the model as routable context: the flow's tool is made
visible and a short note tells the model to continue it when the user is
engaging, or handle the request normally otherwise.

Continuing a flow is then ordinary tool selection — the model calls the flow's
tool, whose ``get_tools()`` forwards to the IA's ``execute(visitor)``, which
loads and advances its own active session.
"""

from __future__ import annotations

import logging
from typing import Any, FrozenSet, Optional, Set

logger = logging.getLogger(__name__)

# Task types that are not turn-spanning flows (proactive outreach, etc.).
_NON_FLOW_TASK_TYPES = frozenset({"PROACTIVE", "AGENTIC_LOOP"})


def _store(conversation: Any) -> Optional[Any]:
    if conversation is None:
        return None
    try:
        from jvagent.memory.task_store import TaskStore

        return TaskStore(conversation)
    except Exception as exc:  # pragma: no cover - import wiring
        logger.debug("continuation: TaskStore unavailable: %s", exc)
        return None


def active_flow_owner(
    visitor: Any,
    *,
    flow_tool_names: Optional[Set[str]] = None,
) -> Optional[str]:
    """Return the ``owner_action`` of an active flow control-task, or ``None``.

    The owner_action equals the IA's class name, which is also its tool name in
    the SkillExecutive's surface (the IA's own ``get_tools()`` names it).

    Filters out non-flow tasks (e.g. ``PROACTIVE``) and, when
    ``flow_tool_names`` is supplied, only returns an owner that maps to a
    routable IA tool on the agent surface.
    """
    conversation = getattr(visitor, "conversation", None)
    store = _store(conversation)
    if store is None:
        return None
    try:
        active = store.list(status="active")
    except Exception as exc:
        logger.debug("continuation: list(active) failed: %s", exc)
        return None
    names: FrozenSet[str] = frozenset(flow_tool_names or ())
    candidates: list[tuple[str, str]] = []
    for th in active or []:
        task_type = (getattr(th, "task_type", None) or "").strip().upper()
        if task_type in _NON_FLOW_TASK_TYPES:
            continue
        owner = getattr(th, "owner_action", None)
        if not owner:
            continue
        owner_str = str(owner)
        if names and owner_str not in names:
            continue
        updated_at = str(getattr(th, "updated_at", "") or "")
        candidates.append((updated_at, owner_str))
    if not candidates:
        return None
    # When multiple flows are active, prefer the most recently updated task.
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def active_flow_note(tool_name: str) -> str:
    """A system note telling the model how to treat an in-progress flow."""
    return (
        f"A multi-step flow is in progress (tool: `{tool_name}`). If the user's "
        f"message is engaging with it — answering, continuing, confirming, or "
        f"cancelling — call `{tool_name}` to continue the flow. If the user has "
        f"changed topic or asked something unrelated, handle that request "
        f"normally with the other tools; the flow stays active and resumes when "
        f"the user returns to it."
    )


__all__ = ["active_flow_owner", "active_flow_note"]
