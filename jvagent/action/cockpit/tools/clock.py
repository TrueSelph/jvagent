"""Clock harness tool for cockpit.

Exposes the current date and time so the model doesn't have to guess. Time
is rendered in the configured app timezone (``App.timezone`` — see
``jvagent.core.app``); falls back to UTC if the app or zone is unavailable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from jvagent.action.cockpit.context import CockpitContext
from jvagent.tooling.tool import Tool


async def _resolve_now() -> datetime:
    """Return current datetime in the app timezone, or UTC as a fallback."""
    try:
        from jvagent.core.app import App

        app = await App.get()
        if app is not None:
            now = await app.now()
            if isinstance(now, datetime):
                return now
    except Exception:
        pass
    return datetime.now(timezone.utc)


def _build_clock_tools(ctx: CockpitContext) -> List[Tool]:
    """Return harness tools that expose the current date/time."""

    async def _get_current_datetime() -> str:
        now = await _resolve_now()
        tz = getattr(now.tzinfo, "key", None) or (
            str(now.tzinfo) if now.tzinfo else "naive"
        )
        return (
            f"{now.isoformat()} " f"(weekday={now.strftime('%A')}, " f"timezone={tz})"
        )

    return [
        Tool(
            name="get_current_datetime",
            description=(
                "Return the current date, time, weekday, and timezone in ISO 8601 "
                "format. Use this whenever you need to reason about 'now', "
                "'today', schedule something relative to the present, or report "
                "the time to the user. Do not guess — call this tool."
            ),
            parameters_schema={"type": "object", "properties": {}, "required": []},
            execute=_get_current_datetime,
        ),
    ]
