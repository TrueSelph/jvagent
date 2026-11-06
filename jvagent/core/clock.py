"""The app clock as a service, independent of any harness execution engine."""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


async def read_app_clock(app: Any) -> str:
    """Read fresh app time with an explicit offset; never invent a fallback date."""
    if app is None:
        raise RuntimeError("App clock is unavailable")
    configured = getattr(app, "timezone", "")
    if configured is None:  # App's declared default means server-local time.
        configured = ""
    if not isinstance(configured, str):
        raise ValueError("App timezone is invalid")
    try:
        zone = ZoneInfo(configured) if configured else None
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("App timezone is invalid") from None
    now = await app.now()
    if not isinstance(now, datetime):
        raise ValueError("App clock did not return a timestamp")
    if zone is not None and (now.tzinfo is None or now.utcoffset() is None):
        raise ValueError("Configured app clock must return timezone-aware time")
    now = now.astimezone(zone) if zone is not None else now.astimezone()
    label = zone.key if zone is not None else now.tzname() or str(now.tzinfo)
    return (
        f"ISO 8601: {now.isoformat()}\n"
        f"Date: {now.strftime('%A, %B %d, %Y')}\n"
        f"Time: {now.strftime('%H:%M:%S')}\nTimezone: {label}"
    )
