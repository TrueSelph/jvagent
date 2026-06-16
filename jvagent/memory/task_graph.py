"""Generic work-graph primitives over the TaskStore (ADR-0026).

Domain-agnostic, type-agnostic: prerequisites push (``blocked_on`` edges),
completion unblocks, and the orchestrator resolves the *top runnable* task. These
are the foundation the turn-lock resolver and drain loop build on; the proactive
queue (ADR-0022) layers schedule/event eligibility on the same idea.

A task is **runnable** when it is non-terminal (``pending``/``active``) and every
``blocked_on`` prerequisite is ``completed``. "Blocked" is *derived* (has an
incomplete prerequisite), not a status — the state machine is unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Collection, List, Optional

if TYPE_CHECKING:
    from jvagent.memory.task_store import TaskHandle, TaskStore

# Non-terminal = the task still represents outstanding work.
NON_TERMINAL_STATUSES = frozenset({"pending", "active"})

# PROACTIVE tasks are eligibility-gated by the scheduler (ADR-0022): a pending
# proactive task is *queued*, not due — schedule/event eligibility is the
# scheduler's concern, and it claims the task (pending → active) only when due. So
# a PROACTIVE task is runnable for the generic resolver ONLY while ``active``; every
# other type is runnable while pending or active. This is what lets one work graph
# hold both interactive work and scheduled work without a queued proactive task
# firing on an ordinary turn.
_PROACTIVE_TASK_TYPE = "PROACTIVE"


def _status_runnable(handle: "TaskHandle") -> bool:
    status = getattr(handle, "status", "")
    if status not in NON_TERMINAL_STATUSES:
        return False
    ttype = str(getattr(handle, "task_type", "") or "").upper()
    if ttype == _PROACTIVE_TASK_TYPE:
        return status == "active"  # claimed/due, not merely queued
    return True


def prerequisites_met(store: "TaskStore", handle: "TaskHandle") -> bool:
    """True when every ``blocked_on`` prerequisite is completed (missing deps are
    treated as satisfied so a swept/deleted prerequisite never deadlocks)."""
    for tid in getattr(handle, "blocked_on", None) or []:
        dep = store.get(str(tid))
        if dep is not None and dep.status != "completed":
            return False
    return True


def is_runnable(store: "TaskStore", handle: "TaskHandle") -> bool:
    """Runnable now: non-terminal (claimed, for PROACTIVE) and prerequisites met."""
    return _status_runnable(handle) and prerequisites_met(store, handle)


def has_outstanding_work(
    store: "TaskStore", *, task_types: Optional[Collection[str]] = None
) -> bool:
    """Engagement state (ADR-0026 invariant 7): any non-terminal task exists.

    While True, the orchestrator is *engaged* (not idle) — it re-enters and keeps
    draining on the next signal. Optionally scope to specific ``task_types``.
    """
    types = {t.upper() for t in task_types} if task_types else None
    for h in store.list():
        if not _status_runnable(h):
            continue  # terminal, or a queued (pending) proactive task
        if types and str(getattr(h, "task_type", "") or "").upper() not in types:
            continue
        return True
    return False


def _runnable_sort_key(handle: "TaskHandle") -> tuple:
    data = getattr(handle, "data", {}) or {}
    priority = int(data.get("priority") or 0)
    order = int(getattr(handle, "order", 0) or 0)
    task = getattr(handle, "_task", None)
    created = str(getattr(task, "created_at", "") or "") if task else ""
    # Highest priority first; then FIFO by order, then creation. In a linear
    # prerequisite chain only the deepest task (no incomplete blockers) is runnable,
    # so ties only arise among genuinely independent flows.
    return (-priority, order, created)


def pick_top_runnable(
    store: "TaskStore", *, task_types: Optional[Collection[str]] = None
) -> Optional["TaskHandle"]:
    """The top runnable task to own this turn: non-terminal, prerequisites met,
    highest priority then FIFO. ``None`` when the store has no runnable work."""
    types = {t.upper() for t in task_types} if task_types else None
    candidates: List["TaskHandle"] = []
    for h in store.list(status=["pending", "active"]):
        if types and str(getattr(h, "task_type", "") or "").upper() not in types:
            continue
        if not _status_runnable(h):
            continue  # e.g. a PROACTIVE task still queued (pending), not yet claimed
        if not prerequisites_met(store, h):
            continue
        candidates.append(h)
    if not candidates:
        return None
    candidates.sort(key=_runnable_sort_key)
    return candidates[0]
