"""Core tool surface for the Orchestrator (ADR-0012 §2.2).

Always-available tools that wrap harness services, independent of which actions
are installed. Kept deliberately small and dependency-light; extend by adding
builders to :func:`build_core_tools`. Persona ``reply``/``respond`` are NOT here
— they come from the agent's responder via ``get_responder().get_tools()``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from jvagent.action.orchestrator.proactive_tools import build_proactive_tools
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


# List-key aliases a model reaches for instead of the canonical ``steps``.
_PLAN_LIST_KEYS = (
    "steps",
    "plan",
    "tasks",
    "items",
    "checklist",
    "todos",
    "todo",
    "plan_steps",
    "list",
)


def _coerce_plan_items(args: Any) -> List[Dict[str, Any]]:
    """Normalize ``update_plan`` args into ``[{description, status, ...}]`` items.

    Deliberately catch-all about the wrapper shape a model emits — only the step
    *content* matters, not which key it landed under:

    - ``args`` itself is the list (no wrapper dict);
    - the step list under a known key (``steps``/``plan``/``tasks``/``items``/
      ``checklist``/``todos``/``todo``/``plan_steps``/``list``) — or, failing
      that, under ANY key whose value is a non-empty list;
    - a dict-of-steps (keyed by index/name) → its values, unwrapping one nested
      ``{steps: [...]}`` level;
    - a single string → a one-step plan;
    - args that themselves describe one step (``step``/``description`` present)
      → a one-step plan.

    Each entry may be a bare string (→ pending step), a mapping with
    ``step``/``description`` + optional ``status``/``result``, or one level of
    accidental list nesting.
    """
    raw: Any = None
    if isinstance(args, (list, tuple)):
        raw = args  # model sent the list directly as the tool args
        args = {}
    elif isinstance(args, dict):
        for key in _PLAN_LIST_KEYS:
            if args.get(key) is not None:
                raw = args[key]
                break
        if raw is None and (args.get("step") or args.get("description")):
            raw = [args]  # one step passed inline, no list wrapper
        if raw is None:
            # Unknown key: take the first list-valued arg, whatever it's called.
            for value in args.values():
                if isinstance(value, (list, tuple)) and value:
                    raw = value
                    break

    # Unwrap a nested mapping: {steps: [...]} inside, else its values.
    if isinstance(raw, dict):
        inner = None
        for key in _PLAN_LIST_KEYS:
            if isinstance(raw.get(key), (list, tuple)):
                inner = raw[key]
                break
        raw = inner if inner is not None else list(raw.values())
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
        elif isinstance(entry, (list, tuple)):
            # One level of accidental nesting: [["a", "b"]] or [[{...}]].
            for sub in entry:
                if isinstance(sub, str) and sub.strip():
                    items.append({"description": sub.strip()})
                elif isinstance(sub, dict):
                    items.append(sub)
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
                "(update_plan needs a non-empty `steps` LIST under the key "
                '`steps`. Example: {"steps": [{"step": "Fetch data", "status": '
                '"done"}, {"step": "Write report", "status": "in_progress"}]}. '
                "Re-send the WHOLE checklist each call. A bare string per step "
                'is also fine: {"steps": ["Fetch data", "Write report"]}.)'
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
            "across turns (so you can resume if interrupted). Argument shape: a "
            "single key `steps` holding a LIST — re-send the WHOLE list every "
            'call. Example: {"steps": [{"step": "Research", "status": "done", '
            '"result": "saved sources.md"}, {"step": "Write report", "status": '
            '"in_progress"}]}. Each item is either a bare string (a pending '
            "step) or an object with `step` (the text), optional `status` "
            "(pending|in_progress|done|skipped), and optional `result`. On a "
            "completed step set `result` to a short note of what it produced — "
            "especially an artifact path (e.g. 'draft saved to report.md') — so "
            "a later turn reuses that work instead of redoing it. Use for "
            "genuinely multi-step work; skip it for single-step requests."
        ),
        run=_run,
    )


def build_plan_tool(action: Any, visitor: Any) -> SkillTool:
    """Public builder for the ``update_plan`` tool (surfaced only when planning)."""
    return _plan_tool(action, visitor)


def build_artifact_tools(action: Any, visitor: Any) -> List[SkillTool]:
    """``list_artifacts`` / ``get_artifact`` over the conversation's artifact
    registry (ADR-0021). Visitor-bound for conversation access; the model uses
    them to back-reference prior artifacts (e.g. a past image interpretation)
    without re-upload. Returns [] when the conversation has no artifact support.
    """
    conversation = getattr(visitor, "conversation", None)
    if conversation is None or not hasattr(conversation, "get_artifacts"):
        return []

    async def _list(args: Dict[str, Any]) -> str:
        source = (args or {}).get("source") or None
        tag = (args or {}).get("tag") or None
        try:
            items = await conversation.get_artifacts(
                source=source, tags=[tag] if tag else None
            )
        except Exception as exc:  # pragma: no cover - defensive
            return f"(list_artifacts error: {exc})"
        if not items:
            return "(no artifacts)"
        lines = []
        for a in items:
            row = a.index_row()
            lines.append(
                f"- {row['name']} [{row['source']}] "
                f"tags={row['tags']}: {row['summary']}"
            )
        return "Conversation artifacts (call get_artifact to read one):\n" + "\n".join(
            lines
        )

    async def _get(args: Dict[str, Any]) -> str:
        name = ((args or {}).get("name") or "").strip()
        if not name:
            return "(get_artifact requires a 'name')"
        try:
            items = await conversation.get_artifacts(name=name)
        except Exception as exc:  # pragma: no cover - defensive
            return f"(get_artifact error: {exc})"
        if not items:
            return f"(no such artifact: {name})"
        a = items[0]
        return f"{a.name} [{a.source}]:\n{a.data}"

    return [
        SkillTool(
            name="list_artifacts",
            description=(
                "List this conversation's artifacts (names + summaries only). "
                "Optional args: source (e.g. 'vision'), tag. Then call "
                "get_artifact to read the full content of one."
            ),
            run=_list,
        ),
        SkillTool(
            name="get_artifact",
            description=(
                "Read the full content of a conversation artifact by its name "
                '(from list_artifacts). Args: {"name": "<artifact name>"}.'
            ),
            run=_get,
        ),
    ]


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


__all__ = [
    "build_core_tools",
    "build_plan_tool",
    "build_artifact_tools",
    "build_proactive_tools",
]
