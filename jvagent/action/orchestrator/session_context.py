"""Turn-stable session ground truth for the Orchestrator (ADR-0042).

Injected once per turn into the system prompt — same class of environment
facts as the former CURRENT CHANNEL line. Not prep steering: the model still
decides tools; this only removes the need to guess the clock.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


async def render_session_context(
    visitor: Any,
    *,
    app: Any = None,
) -> str:
    """Build the SESSION CONTEXT block for the turn's system prompt.

    Uses ``App.now()`` when an app is available; otherwise UTC wall clock.
    Channel is included when ``visitor.channel`` is set.
    """
    now = await _resolve_now(app)
    tz = getattr(now.tzinfo, "key", None) or (
        str(now.tzinfo) if now.tzinfo else "local"
    )
    lines = [
        "SESSION CONTEXT (authoritative for this turn):",
        f"CURRENT DATE/TIME: {now.strftime('%A, %B %d, %Y')} "
        f"{now.strftime('%H:%M:%S')} ({tz})",
        f"ISO 8601: {now.isoformat()}",
    ]
    channel = str(getattr(visitor, "channel", "") or "").strip()
    if channel:
        lines.append(
            f"CURRENT CHANNEL: {channel}. Every skill listed below is "
            "available on this channel — never tell the user to switch "
            "channels to use one of them."
        )
    lines.append(
        'Relative time ("today", "this year", "yesterday", etc.) MUST use '
        "this clock — never a training cutoff or a guessed year."
    )
    return "\n".join(lines) + "\n\n"


async def _resolve_now(app: Any) -> datetime:
    if app is not None and hasattr(app, "now"):
        try:
            now = await app.now()
            if isinstance(now, datetime):
                return now
        except Exception:
            pass
    if app is None:
        try:
            from jvagent.core.app import App

            app = await App.get()
            if app is not None and hasattr(app, "now"):
                now = await app.now()
                if isinstance(now, datetime):
                    return now
        except Exception:
            pass
    return datetime.now(timezone.utc)
