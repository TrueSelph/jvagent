"""TaskStore-driven onboard skill state (generic, not interview-specific).

A skill listed in the orchestrator ``onboard_skills`` config exits onboard only
when TaskStore has a task with ``owner_action == <skill_name>`` and
``status == completed``. Cancelled/failed tasks do not clear onboard.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from jvagent.action.orchestrator.skills import SkillDoc

logger = logging.getLogger(__name__)

_ONBOARD_ACTIVE_STATUSES = frozenset({"pending", "active"})


def task_store_for_conversation(conversation: Any) -> Optional[Any]:
    if conversation is None:
        return None
    try:
        from jvagent.memory.task_store import TaskStore

        return TaskStore(conversation)
    except Exception as exc:
        logger.debug("onboard: TaskStore unavailable: %s", exc)
        return None


def tasks_for_skill(store: Any, skill_name: str) -> List[Any]:
    if store is None or not skill_name:
        return []
    try:
        return store.list(owner_action=skill_name) or []
    except Exception as exc:
        logger.debug("onboard: list tasks for %r failed: %s", skill_name, exc)
        return []


def _task_updated_at(handle: Any) -> str:
    task = getattr(handle, "_task", None)
    if task is not None:
        return str(getattr(task, "updated_at", "") or "")
    return str(getattr(handle, "updated_at", "") or "")


def _task_status(handle: Any) -> str:
    task = getattr(handle, "_task", None)
    if task is not None:
        return str(getattr(task, "status", "") or "")
    return str(getattr(handle, "status", "") or "")


def latest_task_for_skill(store: Any, skill_name: str) -> Optional[Any]:
    tasks = tasks_for_skill(store, skill_name)
    if not tasks:
        return None
    return max(tasks, key=_task_updated_at)


def is_onboard_skill_done(store: Any, skill_name: str) -> bool:
    """True only when a skill-named task has reached ``completed``."""
    return any(_task_status(t) == "completed" for t in tasks_for_skill(store, skill_name))


def has_active_onboard_task(store: Any, skill_name: str) -> bool:
    return any(
        _task_status(t) in _ONBOARD_ACTIVE_STATUSES
        for t in tasks_for_skill(store, skill_name)
    )


def pending_onboard_skills(store: Any, skill_names: List[str]) -> List[str]:
    """Skill names still in onboard (no completed task), in config order."""
    return [n for n in skill_names if n and not is_onboard_skill_done(store, n)]


def resolve_onboard_locked_skill_doc(
    visitor: Any,
    skill_docs: List[Any],
    onboard_skill_names: List[str],
    *,
    lock_active_flow: bool,
) -> Optional[Any]:
    """First locked_in onboard skill with an active/pending skill-named task."""
    if not lock_active_flow or not onboard_skill_names:
        return None
    conversation = getattr(visitor, "conversation", None)
    store = task_store_for_conversation(conversation)
    if store is None:
        return None
    skill_by_name = {d.name: d for d in skill_docs if getattr(d, "name", None)}
    for name in onboard_skill_names:
        doc = skill_by_name.get(name)
        if doc is None or not getattr(doc, "locked_in", False):
            continue
        if is_onboard_skill_done(store, name):
            continue
        if has_active_onboard_task(store, name):
            return doc
    return None


def first_pending_locked_onboard_doc(
    skill_docs: List[Any],
    onboard_skill_names: List[str],
    store: Any,
) -> Optional["SkillDoc"]:
    """First locked_in skill in list order that is onboard-pending (not done)."""
    skill_by_name = {d.name: d for d in skill_docs if getattr(d, "name", None)}
    for name in onboard_skill_names:
        doc = skill_by_name.get(name)
        if doc is None or not getattr(doc, "locked_in", False):
            continue
        if not is_onboard_skill_done(store, name):
            return doc
    return None
