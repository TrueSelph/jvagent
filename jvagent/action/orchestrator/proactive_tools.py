"""Orchestrator tools for proactive task queue management."""

from __future__ import annotations

from typing import Any, Dict, List

from jvagent.action.orchestrator.tools import SkillTool
from jvagent.memory.task_proactive import ProactiveTaskSpec, coerce_priority


def _coerce_max_attempts(value: Any, default: int) -> int:
    """Safe int for a model-supplied max_attempts; never raises. Falls back to
    ``default`` for missing/non-numeric input (a model may pass 'three')."""
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def build_proactive_tools(action: Any, visitor: Any) -> List[SkillTool]:
    """Return proactive queue tools when enabled on the orchestrator."""
    if not getattr(action, "proactive_tasks_enabled", True):
        return []

    async def _queue_task(args: Dict[str, Any], _action: Any = action) -> str:
        directive = str(args.get("directive") or "").strip()
        if not directive:
            return "queue_task requires a non-empty directive."

        spec = ProactiveTaskSpec(
            directive=directive,
            context=str(args.get("context") or ""),
            not_before=args.get("not_before"),
            not_after=args.get("not_after"),
            priority=coerce_priority(args.get("priority")),
            skill=(str(args.get("skill")).strip() if args.get("skill") else None),
            requires_tasks=list(args.get("requires_tasks") or []),
            trigger_on=args.get("trigger_on") or "schedule",
            trigger_keyword=args.get("trigger_keyword"),
            trigger_mood=args.get("trigger_mood"),
            max_attempts=_coerce_max_attempts(
                args.get("max_attempts"),
                getattr(_action, "default_max_attempts", 3) or 3,
            ),
        )
        store = getattr(visitor, "tasks", None)
        if store is None:
            return "TaskStore unavailable on this turn."
        handle = await store.enqueue_proactive(
            spec,
            owner_action=_action.get_class_name(),
            title=str(args.get("title") or directive)[:200],
        )
        return (
            f"Queued proactive task {handle.id} "
            f"(status=pending, priority={spec.priority})."
        )

    return [
        SkillTool(
            name="queue_task",
            description=(
                "Queue a proactive task for later Orchestrator execution. "
                "Args: directive (required), title, not_before, not_after, "
                "priority, skill, requires_tasks, trigger_on "
                "(schedule|user_message|keyword|mood|any), trigger_keyword, "
                "trigger_mood, max_attempts."
            ),
            run=_queue_task,
        )
    ]
