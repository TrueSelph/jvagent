"""Eligibility evaluation for proactive TaskStore queue entries."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, List, Optional

from jvagent.memory.task_proactive import (
    PROACTIVE_TASK_TYPE,
    SPEC_VERSION,
    ProactiveTaskSpec,
    coerce_priority,
)

if TYPE_CHECKING:
    from jvagent.memory.task_store import TaskHandle, TaskStore

_SKILL_TASK_TYPE = "SKILL"
_ACTIVE_BLOCKER_STATUSES = frozenset({"pending", "active"})

# A pending/active SKILL task, or an active PROACTIVE spec task, suppresses
# proactive dispatch (conversation_has_blockers). A crash, a serverless timeout,
# or an abandoned flow can leave such a task non-terminal with no lease — and
# nothing sweeps non-terminal tasks — so it would suppress proactive dispatch
# FOREVER. Treat a blocker whose ``updated_at`` is older than this lease as
# stale (non-blocking). This does NOT cancel the task — it only lifts the
# proactive suppression; the flow still resumes when the user returns and its
# ``updated_at`` is bumped on every transition/event, so a genuinely-active flow
# never goes stale. AUDIT-memory MEDIUM (M11).
_DEFAULT_BLOCKER_STALE_SECONDS = 24 * 60 * 60
_BLOCKER_STALE_ENV = "JVAGENT_TASK_BLOCKER_STALE_SECONDS"


def blocker_stale_seconds() -> int:
    """Lease after which a non-terminal task stops suppressing proactive dispatch.

    ``0`` (or negative) disables the lease — blockers suppress indefinitely
    (legacy behavior). Unset/invalid falls back to the 24h default.
    """
    raw = os.environ.get(_BLOCKER_STALE_ENV, "").strip()
    if not raw:
        return _DEFAULT_BLOCKER_STALE_SECONDS
    try:
        return int(raw)
    except ValueError:
        return _DEFAULT_BLOCKER_STALE_SECONDS


def _is_stale_blocker(handle: Any, now: datetime) -> bool:
    """True when *handle* is past its blocker lease (so it must not block)."""
    ttl = blocker_stale_seconds()
    if ttl <= 0:
        return False
    updated = parse_instant(getattr(handle, "updated_at", "") or "")
    if updated is None:
        return False  # no timestamp → can't age it out; keep blocking
    return (now - updated).total_seconds() > ttl


def parse_instant(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 instant to timezone-aware UTC."""
    if not value:
        return None
    text = str(value).strip().replace(" ", "T")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_proactive_spec_task(handle: Any) -> bool:
    if str(getattr(handle, "task_type", "") or "").upper() != PROACTIVE_TASK_TYPE:
        return False
    data = getattr(handle, "data", {}) or {}
    return data.get("spec_version") == SPEC_VERSION and bool(
        str(data.get("directive") or "").strip()
    )


def is_schedule_eligible(spec: ProactiveTaskSpec, now: datetime) -> bool:
    not_before = parse_instant(spec.not_before)
    if not_before is not None and now < not_before:
        return False
    not_after = parse_instant(spec.not_after)
    if not_after is not None and now > not_after:
        return False
    return True


def are_prerequisites_met(store: "TaskStore", spec: ProactiveTaskSpec) -> bool:
    for task_id in spec.requires_tasks or []:
        handle = store.get(str(task_id))
        if handle is None or handle.status != "completed":
            return False
    return True


def is_event_eligible(
    spec: ProactiveTaskSpec,
    interaction: Any,
    *,
    now: datetime,
) -> bool:
    """Evaluate user-turn event triggers (keyword, mood, user_message)."""
    trigger_on = (spec.trigger_on or "schedule").lower()

    if trigger_on == "schedule":
        return False

    if not is_schedule_eligible(spec, now):
        return False

    if trigger_on in ("user_message", "any"):
        utterance = str(getattr(interaction, "utterance", "") or "").strip()
        if utterance:
            return True

    keyword = (spec.trigger_keyword or "").strip().lower()
    if keyword and trigger_on in ("keyword", "any"):
        utterance_lower = str(getattr(interaction, "utterance", "") or "").lower()
        if keyword in utterance_lower:
            return True

    mood = (spec.trigger_mood or "").strip().lower()
    if mood and trigger_on in ("mood", "any"):
        monologue = str(getattr(interaction, "inner_monologue", "") or "").lower()
        mood_match = re.search(r"mood[:\s]+(\w+)", monologue, re.IGNORECASE)
        if mood_match and mood_match.group(1).lower() == mood:
            return True

    return False


def conversation_has_blockers(
    store: "TaskStore", *, now: Optional[datetime] = None
) -> bool:
    now_dt = now or datetime.now(timezone.utc)
    for handle in store.list():
        status = str(getattr(handle, "status", "") or "")
        task_type = str(getattr(handle, "task_type", "") or "").upper()
        blocking = (
            task_type == _SKILL_TASK_TYPE and status in _ACTIVE_BLOCKER_STATUSES
        ) or (
            task_type == PROACTIVE_TASK_TYPE
            and status == "active"
            and is_proactive_spec_task(handle)
        )
        # A stale (orphaned) blocker must not suppress proactive dispatch forever.
        if blocking and not _is_stale_blocker(handle, now_dt):
            return True
    return False


def _queue_sort_key(handle: Any) -> tuple:
    data = getattr(handle, "data", {}) or {}
    priority = coerce_priority(data.get("priority"))
    task = getattr(handle, "_task", None)
    created = str(getattr(task, "created_at", "") or "") if task else ""
    return (-priority, created)


def pick_next_proactive_task(
    store: "TaskStore",
    *,
    interaction: Any = None,
    now: Optional[datetime] = None,
) -> Optional["TaskHandle"]:
    """Return the highest-priority eligible pending PROACTIVE task, if any."""
    now_dt = now or datetime.now(timezone.utc)
    if conversation_has_blockers(store, now=now_dt):
        return None

    candidates: List[Any] = []
    for handle in store.list(status="pending"):
        if not is_proactive_spec_task(handle):
            continue
        try:
            spec = ProactiveTaskSpec.from_task_handle(handle)
        except ValueError:
            continue
        if not are_prerequisites_met(store, spec):
            continue

        if interaction is not None:
            if not is_event_eligible(spec, interaction, now=now_dt):
                continue
        else:
            if spec.trigger_on != "schedule":
                continue
            if not is_schedule_eligible(spec, now_dt):
                continue

        candidates.append(handle)

    if not candidates:
        return None
    candidates.sort(key=_queue_sort_key)
    return candidates[0]
