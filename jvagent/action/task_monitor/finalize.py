"""Post-turn finalization for proactive TaskMonitor dispatches."""

from __future__ import annotations

import logging
from typing import Any, Optional

from jvagent.memory.task_eligibility import parse_instant
from jvagent.memory.task_proactive import ProactiveTaskSpec
from jvagent.memory.task_store import TaskStore

logger = logging.getLogger(__name__)


async def cancel_expired_pending(store: TaskStore, *, now: Any) -> int:
    """Cancel pending proactive tasks past ``not_after``."""
    cancelled = 0
    for handle in store.list_queue(statuses=("pending",)):
        try:
            spec = ProactiveTaskSpec.from_task_handle(handle)
        except ValueError:
            continue
        not_after = parse_instant(spec.not_after)
        if not_after is not None and now > not_after:
            await handle.cancel(reason="expired")
            cancelled += 1
    return cancelled


async def sweep_terminal_proactive(
    store: TaskStore,
    *,
    ttl_days: int = 30,
    now: Any = None,
) -> int:
    """Remove terminal PROACTIVE tasks older than ``ttl_days`` (0 disables)."""
    if ttl_days <= 0:
        return 0
    from datetime import datetime, timedelta, timezone

    from jvagent.memory.task_proactive import PROACTIVE_TASK_TYPE

    now_dt = now or datetime.now(timezone.utc)
    cutoff = now_dt - timedelta(days=int(ttl_days))
    removed = 0
    terminal = frozenset(
        {
            "completed",
            "failed",
            "cancelled",
            "timed_out",
            "max_iterations",
            "superseded",
        }
    )
    kept: list = []
    for handle in store.list():
        if handle.task_type != PROACTIVE_TASK_TYPE or handle.status not in terminal:
            kept.append(handle._task.to_dict())
            continue
        task = handle._task
        terminal_at = getattr(task, "terminal_at", None) or getattr(
            task, "updated_at", None
        )
        parsed = parse_instant(str(terminal_at) if terminal_at else None)
        if parsed is None or parsed > cutoff:
            kept.append(task.to_dict())
            continue
        removed += 1
    if removed:
        store._conversation.tasks = kept
        await store._persist()
    return removed


async def finalize_proactive_task(
    store: TaskStore,
    task_id: str,
    *,
    interaction: Optional[Any] = None,
    error: Optional[BaseException] = None,
) -> str:
    """Complete, requeue, or fail a dispatched proactive task.

    Returns the terminal action taken: ``completed``, ``requeued``, ``failed``,
    or ``skipped``.
    """
    handle = store.get(task_id)
    if handle is None or handle.status != "active":
        return "skipped"

    try:
        spec = ProactiveTaskSpec.from_task_handle(handle)
    except ValueError:
        await handle.fail(reason="invalid proactive spec")
        return "failed"

    if error is not None:
        if int(spec.attempt_count or 0) + 1 < int(spec.max_attempts or 1):
            await store.requeue_proactive(task_id, reason=str(error))
            return "requeued"
        await handle.fail(reason=str(error))
        return "failed"

    response = ""
    if interaction is not None:
        response = str(getattr(interaction, "response", "") or "").strip()

    if not response:
        if int(spec.attempt_count or 0) + 1 < int(spec.max_attempts or 1):
            await store.requeue_proactive(task_id, reason="empty orchestrator response")
            return "requeued"
        await handle.fail(reason="empty orchestrator response")
        return "failed"

    await handle.complete(result=response[:500])
    return "completed"
