"""Active-flow awareness for the Orchestrator (ADR-0012).

Turn-spanning flows (e.g. a locked-in skill) record an active control-task on
the conversation ``TaskStore`` while running. The Orchestrator does **not**
force every turn back into the flow when ``lock_active_flow`` is off — that
would shove off-topic utterances into an active locked flow. Instead it surfaces
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

# Task types that are not turn-spanning IA flows. ``PROACTIVE`` is outreach;
# ``AGENTIC_LOOP`` is the orchestrator's own resumable multi-step plan (ADR-0019)
# — it has no IA tool to route to, so it is excluded from IA-flow routing here
# and resumed instead via ``active_plan`` / ``plan_resume_note`` below.
_NON_FLOW_TASK_TYPES = frozenset({"PROACTIVE", "AGENTIC_LOOP"})

# Task type the orchestrator uses for its own resumable multi-step plan.
PLAN_TASK_TYPE = "AGENTIC_LOOP"


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
    the Orchestrator's surface (the IA's own ``get_tools()`` names it).

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


def active_plan(visitor: Any, *, owner: Optional[str] = None) -> Optional[Any]:
    """Return the active orchestrator-owned plan ``TaskHandle``, or ``None``.

    A plan is an active ``AGENTIC_LOOP`` control-task (ADR-0019). When ``owner``
    is given, only a task whose ``owner_action`` matches it is returned. When
    several are active (shouldn't happen — ``update_plan`` overwrites the single
    plan), the most recently updated wins.
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
    candidates: list[tuple[str, Any]] = []
    for th in active or []:
        task_type = (getattr(th, "task_type", None) or "").strip().upper()
        if task_type != PLAN_TASK_TYPE:
            continue
        if owner and str(getattr(th, "owner_action", "") or "") != owner:
            continue
        updated_at = str(getattr(th, "updated_at", "") or "")
        candidates.append((updated_at, th))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def plan_resume_note(plan: Any) -> str:
    """A system note re-grounding the model on an in-progress multi-step plan.

    Soft, like :func:`active_flow_note`: it surfaces the persisted checklist as
    context and tells the model to continue from the first unfinished step, not
    to redo completed ones, and that the plan stays parked if the user changes
    topic. Returns ``""`` when there is nothing actionable to resume.
    """
    if plan is None:
        return ""
    try:
        if not plan.has_pending_steps():
            return ""
        checklist = plan.format_plan()
    except Exception:
        return ""
    if not checklist or checklist == "(no steps)":
        return ""
    return (
        "A multi-step plan you recorded on an earlier turn is still in "
        "progress:\n"
        f"{checklist}\n\n"
        "Continue from the first unfinished step — do NOT redo completed steps. "
        "Keep it updated with update_plan as you finish steps, and when the last "
        "step is done the plan closes automatically. If the user has changed "
        "topic, handle that instead; the plan stays parked and resumes when they "
        "return to it."
    )


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


async def cancel_orphan_flow_tasks(
    visitor: Any,
    *,
    routable_tool_names: Optional[Set[str]] = None,
    locked_skill_names: Optional[Set[str]] = None,
) -> int:
    """Cancel active flow tasks whose owner is no longer routable on the surface.

    Tasks owned by a locked-in skill (``locked_skill_names``) are **exempt**
    from sweeping — they are not IA tools but are intentionally kept alive
    until the skill itself marks the task complete or cancelled.
    """
    conversation = getattr(visitor, "conversation", None)
    store = _store(conversation)
    if store is None:
        return 0
    names: FrozenSet[str] = frozenset(routable_tool_names or ())
    exempt: FrozenSet[str] = frozenset(locked_skill_names or ())
    cancelled = 0
    try:
        active = store.list(status="active")
    except Exception as exc:
        logger.debug("continuation: list(active) for sweep failed: %s", exc)
        return 0
    for th in active or []:
        task_type = (getattr(th, "task_type", None) or "").strip().upper()
        if task_type in _NON_FLOW_TASK_TYPES:
            continue
        owner = str(getattr(th, "owner_action", "") or "")
        # Never sweep tasks owned by a locked-in skill — they persist until
        # the bound action marks them complete/cancelled.
        if owner and owner in exempt:
            continue
        if not owner or (names and owner not in names):
            try:
                await th.cancel(reason="orphan flow task — owner unroutable")
                cancelled += 1
            except Exception as exc:
                logger.debug(
                    "continuation: cancel orphan flow %s failed: %s", owner, exc
                )
    return cancelled


__all__ = [
    "active_flow_owner",
    "active_flow_note",
    "active_plan",
    "plan_resume_note",
    "PLAN_TASK_TYPE",
    "cancel_orphan_flow_tasks",
]
