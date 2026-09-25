"""Turn-stable session ground truth for the Orchestrator (ADR-0042 / ADR-0056).

Injected once per turn into the system prompt — same class of environment
facts as the former CURRENT CHANNEL line. Not prep steering: the model still
decides tools; this only removes the need to guess the clock.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List, Optional

# Hosts may put a pre-rendered prose block on visitor.data under this key.
# Harness appends it verbatim to SESSION CONTEXT — no schema inspection
# (ADR-0056). Cap so a buggy host cannot blow the system prompt.
SESSION_CONTEXT_EXTRA_KEY = "session_context_extra"
_MAX_SESSION_CONTEXT_EXTRA_CHARS = 2000


def normalize_session_context_extra(raw: Any) -> Optional[str]:
    """Return a trimmed host extra block, or None if empty / unusable."""
    if raw is None:
        return None
    if isinstance(raw, str):
        text = raw.strip()
    elif isinstance(raw, (list, tuple)):
        parts: List[str] = []
        for item in raw:
            piece = str(item or "").strip()
            if piece:
                parts.append(piece)
        text = "\n".join(parts).strip()
    else:
        return None
    if not text:
        return None
    if len(text) > _MAX_SESSION_CONTEXT_EXTRA_CHARS:
        text = text[: _MAX_SESSION_CONTEXT_EXTRA_CHARS - 1] + "…"
    return text


async def render_session_context(
    visitor: Any,
    *,
    app: Any = None,
) -> str:
    """Build the SESSION CONTEXT block for the turn's system prompt.

    Uses ``App.now()`` when an app is available; otherwise UTC wall clock.
    Channel is included when ``visitor.channel`` is set.
    Optional host prose via ``visitor.data["session_context_extra"]`` (ADR-0056).
    """
    now = await _resolve_now(app)
    tz = getattr(now.tzinfo, "key", None) or (
        str(now.tzinfo) if now.tzinfo else "local"
    )
    abbrev = now.strftime("%Z")
    zone = f"{tz}, {abbrev}" if abbrev and abbrev != tz else tz
    lines = [
        "SESSION CONTEXT (authoritative for this turn):",
        f"CURRENT DATE/TIME: {now.strftime('%A, %B %d, %Y')} "
        f"{now.strftime('%H:%M:%S')} ({zone})",
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
    data = getattr(visitor, "data", None) or {}
    if isinstance(data, dict):
        extra = normalize_session_context_extra(data.get(SESSION_CONTEXT_EXTRA_KEY))
        if extra:
            lines.append(extra)
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
