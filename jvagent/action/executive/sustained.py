"""Task-backed sustained activation (ADR-0010 §2.5, amended 2026-05-29).

Sustained activation (turn-lock) is persisted on the conversation's declarative
``TaskStore`` rather than an ad-hoc ``context`` key. Two resume sources:

1. An ``executive_sustained`` Task the Executive wrote for a generic center that
   returned ``RETURN(sustain=True)``.
2. An ``active`` Task owned by a routable rails IA (e.g. an interview). The IA
   already records its own task, so the IA center resumes from *that* — no
   duplicate executive record. This is the unification the context key blocked.

Helpers are best-effort: any error degrades to "no sustained activation".
Imports ``jvagent.memory.task_store`` (neutral) — nothing from bridge/helm.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Set, Tuple

logger = logging.getLogger(__name__)

SUSTAINED_TASK_TYPE = "executive_sustained"
_OWNER = "ExecutiveInteractAction"


def _store(conversation: Any) -> Optional[Any]:
    if conversation is None:
        return None
    try:
        from jvagent.memory.task_store import TaskStore

        return TaskStore(conversation)
    except Exception as exc:  # pragma: no cover - import wiring
        logger.debug("sustained: TaskStore unavailable: %s", exc)
        return None


def _active(store: Any) -> list:
    try:
        return store.list(status="active")
    except Exception as exc:
        logger.debug("sustained: list(active) failed: %s", exc)
        return []


def _ia_owned_active(
    store: Any, centers: Set[str], registry: Any
) -> Optional[Tuple[str, str]]:
    """Return (center, ia_name) for an active task owned by a routable IA a
    loaded center handles, else None."""
    if registry is None or not hasattr(registry, "by_id"):
        return None
    for th in _active(store):
        owner = getattr(th, "owner_action", None)
        if not owner:
            continue
        cap = registry.by_id(owner)
        if cap is not None and cap.center in centers:
            return (cap.center, owner)
    return None


async def read_sustained(
    conversation: Any, centers: Set[str], registry: Any
) -> Optional[Dict[str, Any]]:
    """Return ``{"center", "brief"}`` to resume, or ``None``."""
    store = _store(conversation)
    if store is None:
        return None
    # 1. Executive-written generic sustained task.
    for th in _active(store):
        if getattr(th, "task_type", "") == SUSTAINED_TASK_TYPE:
            data = getattr(th, "data", None) or {}
            center = data.get("center")
            if center in centers:
                return {"center": center, "brief": data.get("brief") or {}}
    # 2. A rails IA's own active task (unification — no duplicate record).
    hit = _ia_owned_active(store, centers, registry)
    if hit is not None:
        center, ia = hit
        return {
            "center": center,
            "brief": {"intent": "", "slots": {"capability": ia}, "constraints": []},
        }
    return None


async def has_active_ia_task(
    conversation: Any, centers: Set[str], registry: Any
) -> bool:
    """True iff a rails IA the IA center handles has an active task."""
    store = _store(conversation)
    if store is None:
        return False
    return _ia_owned_active(store, centers, registry) is not None


async def write_sustained(
    conversation: Any, *, center: str, brief: Dict[str, Any]
) -> None:
    """Create/update the single ``executive_sustained`` active task."""
    store = _store(conversation)
    if store is None:
        return
    try:
        for th in store.list(status=["pending", "active"]):
            if getattr(th, "task_type", "") == SUSTAINED_TASK_TYPE:
                await th.update(center=center, brief=brief)
                return
        handle = await store.create(
            title="executive sustained activation",
            description=str(center),
            task_type=SUSTAINED_TASK_TYPE,
            owner_action=_OWNER,
            data={"center": center, "brief": brief},
        )
        await handle.start()
    except Exception as exc:
        logger.debug("sustained: write failed: %s", exc)


async def clear_sustained(conversation: Any) -> None:
    """Cancel any active ``executive_sustained`` task (leaves IA-owned tasks)."""
    store = _store(conversation)
    if store is None:
        return
    try:
        for th in store.list(status=["pending", "active"]):
            if getattr(th, "task_type", "") == SUSTAINED_TASK_TYPE:
                await th.cancel(reason="executive: turn complete")
    except Exception as exc:
        logger.debug("sustained: clear failed: %s", exc)


__all__ = [
    "SUSTAINED_TASK_TYPE",
    "read_sustained",
    "write_sustained",
    "clear_sustained",
    "has_active_ia_task",
]
