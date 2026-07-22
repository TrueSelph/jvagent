"""ADR-0034 interview staleness reaper.

Rides the TaskMonitor tick (same infrastructure as the QUO-2 job reaper). For
each interview SKILL task it measures idle time from the task's ``updated_at``
and applies the skill's declared TTL policy:

- ``nudge_after``: send ONE proactive reminder, ever (the nudge refreshes the
  idle clock, so ``abandon_after`` is then measured from the nudge).
- ``abandon_after``: apply the skill's ``on_abandon`` (park | cancel).
- ``parked_expire_after``: a parked snapshot eventually closes ``cancelled``
  (no message — the user is long gone; the next contact routes fresh).

Rails: the reaper never touches a task that (a) is blocked on a prerequisite
(ADR-0026 gated parent — it is waiting, not idle) or (b) is not an
interview-managed SKILL task. The in-flight-turn rail is provided by the tick,
which runs the reaper under the per-conversation mutation lock.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from .session import clear_interview_context, load_session
from .spec import InterviewSpec, parse_duration_seconds
from .tasks import TASK_TYPE

logger = logging.getLogger(__name__)

# Task.data marker so a nudge is sent at most once per task.
NUDGE_SENT_KEY = "abandon_nudge_sent"

SpecLookup = Callable[[str], Optional[InterviewSpec]]
Sender = Callable[[str], Awaitable[None]]


def idle_seconds(updated_at: str, now: datetime) -> Optional[float]:
    """Seconds since ``updated_at`` (ISO-8601). None when unparseable/empty."""
    if not updated_at:
        return None
    try:
        dt = datetime.fromisoformat(updated_at)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds()


def classify_reap_action(
    status: str, idle: Optional[float], spec: InterviewSpec, already_nudged: bool
) -> Optional[str]:
    """Decide the reaper action from status + idle + the spec's TTL policy.

    Returns one of ``"nudge"``, ``"abandon"``, ``"expire"`` or ``None``.
    ``abandon`` wins over ``nudge`` when both thresholds are crossed.
    """
    if spec is None or idle is None:
        return None
    if status == "parked":
        ttl = parse_duration_seconds(spec.parked_expire_after)
        if ttl is not None and idle >= ttl:
            return "expire"
        return None
    if status == "active":
        abandon = parse_duration_seconds(spec.abandon_after)
        if abandon is not None and idle >= abandon:
            return "abandon"
        nudge = parse_duration_seconds(spec.nudge_after)
        if nudge is not None and idle >= nudge and not already_nudged:
            return "nudge"
    return None


def _nudge_message(spec: InterviewSpec) -> str:
    title = spec.title or spec.name.replace("_", " ")
    return (
        f"Still want to finish your {title}? Pick up where we left off any time "
        "— or ignore this and I'll set it aside."
    )


def _is_interview_managed(handle: Any) -> bool:
    if str(getattr(handle, "task_type", "") or "").upper() != TASK_TYPE:
        return False
    data = getattr(handle, "data", None) or {}
    return isinstance(data, dict) and bool(
        data.get("interview_managed") or data.get("interview_type")
    )


async def reap_interview_tasks(
    conversation: Any,
    store: Any,
    spec_lookup: SpecLookup,
    now: datetime,
    *,
    send: Optional[Sender] = None,
) -> dict:
    """Sweep one conversation's interview SKILL tasks and apply TTL policy.

    ``spec_lookup(owner_action)`` resolves the interview spec (for TTLs +
    on_abandon). ``send(text)`` delivers a proactive nudge to the channel; when
    omitted, nudges are skipped (but still marked, to avoid a later burst).
    Returns a small counts dict for observability/testing.
    """
    counts = {"nudged": 0, "abandoned": 0, "expired": 0}
    try:
        handles = list(store.list(status=["active", "parked"]) or [])
    except Exception as exc:
        logger.debug("reaper: task list failed: %s", exc)
        return counts

    for handle in handles:
        if not _is_interview_managed(handle):
            continue
        # Rail: a task waiting on a prerequisite is parked-by-design, not idle.
        if list(getattr(handle, "blocked_on", None) or []):
            continue
        owner = str(getattr(handle, "owner_action", "") or "")
        spec = spec_lookup(owner) if owner else None
        if spec is None:
            continue

        status = str(getattr(handle, "status", "") or "")
        idle = idle_seconds(str(getattr(handle, "updated_at", "") or ""), now)
        data = getattr(handle, "data", None) or {}
        already_nudged = isinstance(data, dict) and bool(data.get(NUDGE_SENT_KEY))

        action = classify_reap_action(status, idle, spec, already_nudged)
        if action is None:
            continue

        try:
            if action == "nudge":
                if send is not None:
                    await send(_nudge_message(spec))
                # Mark + refresh idle (update touches updated_at) so abandon_after
                # is measured from the nudge, and the nudge never repeats.
                await handle.update(**{NUDGE_SENT_KEY: True})
                counts["nudged"] += 1
            elif action == "abandon":
                await _apply_abandon(conversation, store, handle, spec)
                counts["abandoned"] += 1
            elif action == "expire":
                await handle.cancel(reason="parked interview expired (ADR-0034)")
                counts["expired"] += 1
        except Exception as exc:
            logger.debug("reaper: action %s failed for %s: %s", action, owner, exc)

    return counts


async def _apply_abandon(
    conversation: Any, store: Any, handle: Any, spec: InterviewSpec
) -> None:
    """Apply the skill's on_abandon policy to an idle active interview task."""
    if (spec.on_abandon or "park").lower() == "cancel":
        if conversation is not None:
            try:
                clear_interview_context(conversation)
            except Exception:
                pass
        await handle.cancel(reason="interview abandoned (ADR-0034 reaper)")
        return

    # park: snapshot the live session (if any) so it can be rehydrated on return,
    # then park the task and clear the live session.
    snapshot = None
    if conversation is not None:
        try:
            sess = load_session(conversation)
            if sess is not None and sess.interview_type == spec.name:
                snapshot = sess.to_dict()
        except Exception:
            snapshot = None
    await handle.park(snapshot=snapshot, reason="interview abandoned (ADR-0034 reaper)")
    if conversation is not None:
        try:
            clear_interview_context(conversation)
        except Exception:
            pass
