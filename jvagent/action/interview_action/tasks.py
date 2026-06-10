"""INTERVIEW task lifecycle — turn-lock task tracking on the conversation TaskStore."""

from __future__ import annotations

import logging
from typing import Any, Optional

from .spec import InterviewSpec

logger = logging.getLogger(__name__)

TASK_OWNER_ACTION = "InterviewAction"
TASK_TYPE = "INTERVIEW"

ACTIVE_TASK_DESCRIPTION_TEMPLATE = (
    "The user has engaged the {action_title} (Action Description: {action_description}). "
    "If their latest message is off-topic or unrelated to it, answer that in at most one "
    "short sentence, then steer back and continue the interview — always "
    "ending your reply with the current pending question. Do not abandon the {action_title} until it is "
    "complete or the user explicitly cancels."
)


def task_interview_type(handle: Any) -> Optional[str]:
    task_data = getattr(handle, "data", None) or {}
    if isinstance(task_data, dict):
        raw = task_data.get("interview_type")
        return str(raw) if raw else None
    return None


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
        pass
    try:
        for handle in store.list(status="active", owner_action=TASK_OWNER_ACTION) or []:
            if task_interview_type(handle) == spec_name:
                return handle
    except Exception:
        pass
    return None


async def ensure_active_task(
    visitor: Any, spec: InterviewSpec, default_description: str = ""
) -> None:
    """Create the INTERVIEW task for this spec if missing; close mismatched ones."""
    try:
        if _find_existing_active_task(visitor, spec.name) is not None:
            return
        await close_task(visitor, status="cancelled", exclude_spec_name=spec.name)
        title = spec.title or spec.name.replace("_", " ").title()
        description = ACTIVE_TASK_DESCRIPTION_TEMPLATE.format(
            action_title=title,
            action_description=spec.summary or default_description or "",
        )
        handle = await visitor.tasks.create(
            title=title,
            description=description,
            owner_action=TASK_OWNER_ACTION,
            task_type=TASK_TYPE,
            data={"interview_type": spec.name, "state": "active"},
        )
        await handle.start()
    except Exception as exc:
        logger.debug("ensure_active_task: %s", exc)


async def close_task(
    visitor: Any,
    status: str = "completed",
    spec_name: Optional[str] = None,
    *,
    exclude_spec_name: Optional[str] = None,
) -> None:
    """Close active INTERVIEW tasks.

    With ``spec_name``, only that interview's tasks close. With
    ``exclude_spec_name``, every interview task except that one closes.
    """
    try:
        store = visitor.tasks
        handles = store.list(status="active", owner_action=TASK_OWNER_ACTION) or []
    except Exception:
        return
    for handle in handles:
        it = task_interview_type(handle)
        if spec_name and it != spec_name:
            continue
        if exclude_spec_name and it == exclude_spec_name:
            continue
        try:
            await _apply_task_status(handle, status)
            try:
                await store.delete(handle.id)
            except Exception:
                pass
        except Exception as exc:
            logger.debug("close_task: %s", exc)
    if spec_name:
        try:
            for handle in store.list(status="active", owner_action=spec_name) or []:
                try:
                    await _apply_task_status(handle, status)
                except Exception:
                    pass
        except Exception:
            pass
