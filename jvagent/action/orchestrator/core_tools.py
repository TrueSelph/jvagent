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


__all__ = ["build_core_tools"]
