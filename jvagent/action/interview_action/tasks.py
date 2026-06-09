"""INTERVIEW task lifecycle mixin."""

from __future__ import annotations

import logging
from typing import Any, Optional

from ._constants import (
    ACTIVE_TASK_DESCRIPTION_TEMPLATE,
    TASK_OWNER_ACTION,
    TASK_TYPE,
)
from .core.interview_loader import InterviewSpec
from .handlers._host import InterviewHandlersHost

logger = logging.getLogger(__name__)


async def _apply_task_status(handle: Any, status: str) -> None:
    if status == "completed":
        await handle.complete()
    elif status == "cancelled":
        await handle.cancel()
    elif status == "failed":
        await handle.fail()


class InterviewTaskMixin(InterviewHandlersHost):

    @staticmethod
    def _task_interview_type(handle: Any) -> Optional[str]:
        task_data = getattr(handle, "data", None) or {}
        if isinstance(task_data, dict):
            raw = task_data.get("interview_type")
            return str(raw) if raw else None
        return None

    @staticmethod
    def _find_existing_active_task(
        visitor: Any, spec_name: Optional[str] = None
    ) -> Optional[Any]:
        try:
            store = visitor.tasks
        except Exception:
            return None
        if spec_name:
            try:
                skill_tasks = store.list(status="active", owner_action=spec_name)
                if skill_tasks:
                    return skill_tasks[0]
            except Exception:
                pass
            try:
                for handle in (
                    store.list(status="active", owner_action=TASK_OWNER_ACTION) or []
                ):
                    if InterviewTaskMixin._task_interview_type(handle) == spec_name:
                        return handle
            except Exception:
                pass
            return None
        try:
            existing = store.list(status="active", owner_action=TASK_OWNER_ACTION)
            return existing[0] if existing else None
        except Exception:
            return None

    async def _close_mismatched_interview_tasks(
        self, visitor: Any, spec_name: str, status: str = "cancelled"
    ) -> None:
        try:
            store = visitor.tasks
            handles = store.list(status="active", owner_action=TASK_OWNER_ACTION) or []
        except Exception:
            return
        for handle in handles:
            if self._task_interview_type(handle) == spec_name:
                continue
            try:
                await _apply_task_status(handle, status)
                try:
                    await store.delete(handle.id)
                except Exception:
                    pass
            except Exception as exc:
                logger.debug("_close_mismatched_interview_tasks: %s", exc)

    async def _ensure_active_task(self, visitor: Any, spec: InterviewSpec) -> None:
        if self._find_existing_active_task(visitor, spec.name) is not None:
            return
        await self._close_mismatched_interview_tasks(visitor, spec.name)
        title = spec.title or spec.name.replace("_", " ").title()
        description = ACTIVE_TASK_DESCRIPTION_TEMPLATE.format(
            action_title=title,
            action_description=spec.summary or self.description or "",
        )
        try:
            handle = await visitor.tasks.create(
                title=title,
                description=description,
                owner_action=TASK_OWNER_ACTION,
                task_type=TASK_TYPE,
                data={"interview_type": spec.name, "state": "active"},
            )
            await handle.start()
        except Exception as exc:
            logger.debug("_ensure_active_task: %s", exc)

    async def _close_task(
        self,
        visitor: Any,
        status: str = "completed",
        spec_name: Optional[str] = None,
    ) -> None:
        try:
            store = visitor.tasks
            interview_handles = store.list(
                status="active", owner_action=TASK_OWNER_ACTION
            )
        except Exception:
            return
        for handle in interview_handles or []:
            if spec_name and self._task_interview_type(handle) != spec_name:
                continue
            try:
                await _apply_task_status(handle, status)
                try:
                    await store.delete(handle.id)
                except Exception:
                    pass
            except Exception as exc:
                logger.debug("_close_task: %s", exc)
        if spec_name:
            try:
                for handle in store.list(status="active", owner_action=spec_name) or []:
                    try:
                        await _apply_task_status(handle, status)
                    except Exception:
                        pass
            except Exception:
                pass
