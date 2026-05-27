"""Clock harness tool for the engine.

Exposes the current date and time so the model doesn't have to guess. Time
is rendered in the configured app timezone (``App.timezone`` — see
``jvagent.core.app``); falls back to UTC if the app or zone is unavailable.

The tool also accepts an optional IANA ``timezone`` argument so the model
can answer "what time is it in Tokyo?" without doing arithmetic on a UTC
epoch in its head. The output is a multi-line text block that includes
ISO 8601, weekday, date, time, timezone, and Unix epoch — the model picks
the representation that fits the task.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from jvagent.action.helm.reasoning.context import EngineContext
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


def _convert_to_zone(dt: datetime, target_tz: str) -> datetime:
    """Convert ``dt`` to the requested IANA timezone.

    Raises ``ValueError`` when the zone name is invalid so the tool wrapper
    can surface a clean error to the model.
    """
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    except ImportError:  # pragma: no cover — zoneinfo ships with py3.9+
        raise ValueError("zoneinfo unavailable on this Python build")
    try:
        zone = ZoneInfo(target_tz)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown IANA timezone: {target_tz!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(zone)


def _format_datetime_block(dt: datetime) -> str:
    """Render a datetime as a multi-line block with several representations.

    The model can copy any line into its reply directly. Including the
    ISO 8601 + epoch + weekday on one block lets the model handle date
    math, ordinal references ("next Wednesday"), and explicit timestamps
    without guessing.
    """
    tz_label = getattr(dt.tzinfo, "key", None) or (
        str(dt.tzinfo) if dt.tzinfo else "naive"
    )
    iso = dt.isoformat()
    epoch = int(dt.timestamp()) if dt.tzinfo is not None else 0
    return (
        f"ISO 8601: {iso}\n"
        f"Date: {dt.strftime('%A, %B %d, %Y')}\n"
        f"Time: {dt.strftime('%H:%M:%S')}\n"
        f"Timezone: {tz_label}\n"
        f"Unix epoch (seconds): {epoch}"
    )


def _build_clock_tools(ctx: EngineContext) -> List[Tool]:
    """Return harness tools that expose the current date/time."""

    async def _get_current_datetime(timezone: Optional[str] = None) -> str:
        now = await _resolve_now()
        if timezone and timezone.strip():
            try:
                now = _convert_to_zone(now, timezone.strip())
            except ValueError as exc:
                return f"Error: {exc}"
        return _format_datetime_block(now)

    return [
        Tool(
            name="get_current_datetime",
            description=(
                "Return the current date, time, weekday, timezone, and Unix "
                "epoch — your authoritative source for 'now'. "
                "Call this whenever you need a temporal point of reference: "
                "answering 'what time is it', scheduling something relative "
                "to the present ('tomorrow at 3pm', 'in 2 hours', 'next "
                "Monday'), reasoning about expiry / freshness, computing "
                "ages or durations, or stamping a timestamp into output. "
                "Pass an optional IANA ``timezone`` (e.g. 'America/New_York', "
                "'Europe/London', 'Asia/Tokyo') to convert to that zone — "
                "useful for cross-zone questions ('what time is it in "
                "Tokyo right now?'). Without ``timezone``, returns time in "
                "the app's configured timezone. Never guess the date/time "
                "from training data; always call this tool when temporal "
                "context matters."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": (
                            "Optional IANA timezone name to convert the "
                            "result to (e.g. 'America/New_York', "
                            "'Europe/London', 'Asia/Tokyo', 'UTC'). "
                            "Omit to use the app's default timezone."
                        ),
                    },
                },
                "required": [],
            },
            execute=_get_current_datetime,
        ),
    ]
