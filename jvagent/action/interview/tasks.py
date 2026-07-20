"""Interview SKILL-task lifecycle helpers."""

from __future__ import annotations

import logging
from typing import Any, Optional

from .spec import InterviewSpec

logger = logging.getLogger(__name__)

TASK_TYPE = "SKILL"


async def _apply_task_status(handle: Any, status: str) -> None:
    if status == "completed":
        await handle.complete()
    elif status == "cancelled":
        await handle.cancel()
    elif status == "failed":
        await handle.fail()


def _find_existing_active_task(visitor: Any, spec_name: str) -> Optional[Any]:
    try:
        store = visitor.tasks
    except Exception:
        return None
    try:
        skill_tasks = store.list(status="active", owner_action=spec_name)
        if skill_tasks:
            return skill_tasks[0]
    except Exception:
        return None
    return None


async def _mark_interview_managed(handle: Any, spec_name: str) -> None:
    """Tag a SKILL task so bulk-close only touches interview-managed tasks."""
    task_data = getattr(handle, "data", None) or {}
    current_type = (
        str(task_data.get("interview_type"))
        if isinstance(task_data, dict) and task_data.get("interview_type")
        else None
    )
    managed = (
        bool(task_data.get("interview_managed"))
        if isinstance(task_data, dict)
        else False
    )
    if current_type == spec_name and managed:
        return
    update = {
        "interview_type": spec_name,
        "interview_managed": True,
        "state": "active",
    }
    try:
        await handle.update(**update)
    except Exception:
        # Some mocked handles in tests may not implement update().
        if isinstance(task_data, dict):
            task_data.update(update)


async def ensure_active_task(
    visitor: Any, spec: InterviewSpec, default_description: str = ""
) -> None:
    """Create or tag the active SKILL task for this interview spec."""
    try:
        existing = _find_existing_active_task(visitor, spec.name)
        if existing is not None:
            await _mark_interview_managed(existing, spec.name)
            return
        await close_task(visitor, status="cancelled", exclude_spec_name=spec.name)
        title = spec.title or spec.name.replace("_", " ").title()
        description = spec.summary or default_description or title
        handle = await visitor.tasks.create(
            title=title,
            description=description,
            owner_action=spec.name,
            task_type=TASK_TYPE,
            data={
                "interview_type": spec.name,
                "interview_managed": True,
                "state": "active",
            },
        )
        await handle.start()
    except Exception as exc:
        logger.debug("ensure_active_task: %s", exc)


async def park_task(
    visitor: Any,
    spec_name: str,
    *,
    snapshot: Optional[dict] = None,
    reason: str = "field_unavailable",
) -> bool:
    """Park the active SKILL task for this interview (ADR-0034).

    Snapshots the session onto the task and transitions it active -> parked so it
    owns no turns but can be rehydrated on return. Returns True when a task was
    parked, False when none was found (best-effort — the caller still clears the
    live session and replies)."""
    handle = _find_existing_active_task(visitor, spec_name)
    if handle is None:
        return False
    try:
        await handle.park(snapshot=snapshot, reason=reason)
        return True
    except Exception as exc:
        logger.debug("park_task: %s", exc)
        return False


async def close_task(
    visitor: Any,
    status: str = "completed",
    spec_name: Optional[str] = None,
    *,
    exclude_spec_name: Optional[str] = None,
) -> None:
    """Close interview-managed active SKILL tasks.

    With ``spec_name``, only that interview's tasks close. With
    ``exclude_spec_name``, every interview task except that one closes.
    """
    try:
        store = visitor.tasks
        handles = store.list(status="active") or []
    except Exception:
        return
    for handle in handles:
        owner = str(getattr(handle, "owner_action", "") or "")
        if not owner:
            continue
        task_type = str(getattr(handle, "task_type", "") or "").upper()
        if task_type != TASK_TYPE:
            continue
        if spec_name:
            if owner != spec_name:
                continue
        else:
            task_data = getattr(handle, "data", None) or {}
            managed = isinstance(task_data, dict) and bool(
                task_data.get("interview_managed") or task_data.get("interview_type")
            )
            if not managed:
                continue
            if exclude_spec_name and owner == exclude_spec_name:
                continue
        try:
            await _apply_task_status(handle, status)
        except Exception as exc:
            logger.debug("close_task: %s", exc)
