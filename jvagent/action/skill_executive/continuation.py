"""Active-flow awareness for the SkillExecutive (ADR-0012, model-mediated).

Turn-spanning flows (e.g. the signup interview) record an active control-task on
the conversation ``TaskStore`` while running. The SkillExecutive does **not**
force every turn back into the flow — that would shove off-topic utterances
("Who is Eldon Marks?") into an active interview. Instead it surfaces the active
flow to the model as routable context: the flow's tool is made visible and a
short note tells the model to continue it when the user is engaging, or handle
the request normally otherwise.

Continuing a flow is then ordinary tool selection — the model calls the flow's
tool, whose ``get_tools()`` forwards to the IA's ``execute(visitor)``, which
loads and advances its own active session. No IA-side ``resume`` method, no
deterministic force-resume.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _store(conversation: Any) -> Optional[Any]:
    if conversation is None:
        return None
    try:
        from jvagent.memory.task_store import TaskStore

        return TaskStore(conversation)
    except Exception as exc:  # pragma: no cover - import wiring
        logger.debug("continuation: TaskStore unavailable: %s", exc)
        return None


def active_flow_owner(visitor: Any) -> Optional[str]:
    """Return the ``owner_action`` of an active flow control-task, or ``None``.

    The owner_action equals the IA's class name, which is also its tool name in
    the SkillExecutive's surface (the IA's own ``get_tools()`` names it).
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
    for th in active or []:
        owner = getattr(th, "owner_action", None)
        if owner:
            return str(owner)
    return None


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
