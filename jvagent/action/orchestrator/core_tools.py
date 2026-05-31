"""Core tool surface for the Orchestrator (ADR-0012 §2.2).

Always-available tools that wrap harness services, independent of which actions
are installed. Kept deliberately small and dependency-light; extend by adding
builders to :func:`build_core_tools`. Persona ``reply``/``respond`` are NOT here
— they come from the agent's responder via ``get_responder().get_tools()``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from jvagent.action.orchestrator.tools import SkillTool

logger = logging.getLogger(__name__)


def _datetime_tool(action: Any) -> SkillTool:
    async def _run(args: Dict[str, Any], _action: Any = action) -> str:
        try:
            from datetime import datetime, timezone

            now = None
            get_app = getattr(_action, "get_app", None)
            if callable(get_app):
                app = await get_app()
                if app is not None and hasattr(app, "now"):
                    now = await app.now()
            if not isinstance(now, datetime):
                now = datetime.now(timezone.utc)
        except Exception as exc:  # pragma: no cover - defensive
            return f"(datetime error: {exc})"
        tz = getattr(now.tzinfo, "key", None) or (
            str(now.tzinfo) if now.tzinfo else "UTC"
        )
        return (
            f"ISO 8601: {now.isoformat()}\n"
            f"Date: {now.strftime('%A, %B %d, %Y')}\n"
            f"Time: {now.strftime('%H:%M:%S')}\nTimezone: {tz}"
        )

    return SkillTool(
        name="get_current_datetime",
        description="Get the current authoritative date, time, and timezone.",
        run=_run,
    )


def _coerce_plan_items(args: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize ``update_plan`` args into ``[{description, status}]`` items.

    Accepts ``steps`` or ``plan`` as the list key; each entry may be a bare
    string (→ pending step) or a mapping with ``step``/``description`` and an
    optional ``status``. Tolerant of the shapes a model emits.
    """
    raw = args.get("steps")
    if raw is None:
        raw = args.get("plan")
    if isinstance(raw, str):
        raw = [raw]
    items: List[Dict[str, Any]] = []
    for entry in raw or []:
        if isinstance(entry, str):
            text = entry.strip()
            if text:
                items.append({"description": text})
        elif isinstance(entry, dict):
            items.append(entry)
    return items


def _plan_tool(action: Any, visitor: Any) -> SkillTool:
    """Build the ``update_plan`` tool, bound to this turn's walker.

    Records (or overwrites) the orchestrator's resumable multi-step plan as an
    ``AGENTIC_LOOP`` control-task on the conversation TaskStore, owned by the
    orchestrator. Full-state overwrite: the model re-sends its whole checklist
    each call. The plan persists across turns so an interrupted multi-step turn
    can resume instead of re-planning (ADR-0019).
    """
    owner = action.get_class_name()

    async def _run(
        args: Dict[str, Any], _action: Any = action, _visitor: Any = visitor
    ) -> str:
        from jvagent.action.orchestrator.continuation import active_plan

        items = _coerce_plan_items(args)
        if not items:
            return (
                "(update_plan needs a non-empty `steps` list, e.g. "
                'steps=["Fetch data", "Summarize", "Write report"].)'
            )
        conversation = getattr(_visitor, "conversation", None)
        if conversation is None:
            return "(update_plan unavailable: no conversation)"
        from jvagent.memory.task_store import TaskStore

        store = TaskStore(conversation)
        try:
            handle = active_plan(_visitor, owner=owner)
            if handle is None:
                title = str(args.get("title") or "Multi-step plan").strip()
                handle = await store.create(
                    title=title[:120] or "Multi-step plan",
                    description=title[:500] or "Multi-step plan",
                    owner_action=owner,
                    task_type="AGENTIC_LOOP",
                )
                await handle.start()
            await handle.sync_plan(items)
            refreshed = store.get(handle.id) or handle
            return "Plan recorded — continue working it:\n" + refreshed.format_plan()
        except Exception as exc:
            logger.warning("update_plan: failed: %s", exc)
            return f"(update_plan error: {exc})"

    return SkillTool(
        name="update_plan",
        description=(
            "Record or update your multi-step plan as a checklist that PERSISTS "
            "across turns (so you can resume if interrupted). Pass the full "
            "`steps` list every call (each: a string, or {step, status} where "
            "status is pending|in_progress|done|skipped). Use for genuinely "
            "multi-step work; skip it for single-step requests."
        ),
        run=_run,
    )


def build_plan_tool(action: Any, visitor: Any) -> SkillTool:
    """Public builder for the ``update_plan`` tool (surfaced only when planning)."""
    return _plan_tool(action, visitor)


# Each core tool's minimum tier. minimal < standard < full; a tool is included
# when the configured tier is at least its minimum.
_TIER_RANK = {"minimal": 0, "standard": 1, "full": 2}
_CORE_TOOL_TIERS = {"get_current_datetime": "standard"}


def build_core_tools(action: Any, tier: str = "standard") -> List[SkillTool]:
    """Return the always-available core tools, bound to the orchestrator action.

    ``tier`` (minimal | standard | full) gates which core tools are surfaced;
    unknown values fall back to ``standard``.
    """
    rank = _TIER_RANK.get((tier or "standard").strip().lower(), 1)
    candidates = [_datetime_tool(action)]
    return [
        t
        for t in candidates
        if _TIER_RANK.get(_CORE_TOOL_TIERS.get(t.name, "minimal"), 0) <= rank
    ]


__all__ = ["build_core_tools", "build_plan_tool"]
