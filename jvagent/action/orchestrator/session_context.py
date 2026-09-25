"""Turn-stable session ground truth for the Orchestrator (ADR-0042 / ADR-0056).

Injected once per turn into the system prompt — same class of environment
facts as the former CURRENT CHANNEL line. Not prep steering: the model still
decides tools; this only removes the need to guess the clock or invent where
the user is in a host UI.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


_MAX_PATH_CHARS = 200
_MAX_CRUMB_CHARS = 160
_MAX_LABEL_CHARS = 80


def format_ui_route(ctx: Any) -> Optional[str]:
    """Render host UI route facts for SESSION CONTEXT (ADR-0056).

    Accepts the Integral ``page_context`` snapshot shape (``page_kind``,
    ``breadcrumbs``, ``focused_*``, ``metadata`` titles) and, as a thin
    fallback, messenger-style ``title`` + ``path``. Returns ``None`` when
    nothing useful is present — callers omit the block entirely.

    Facts only: no tool names, no next-step cues (thin-harness invariant 3).
    """
    if not isinstance(ctx, dict) or not ctx:
        return None

    kind = _clip(str(ctx.get("page_kind") or "").strip(), _MAX_LABEL_CHARS)
    path = _clip(
        str(ctx.get("route_path") or ctx.get("url") or ctx.get("path") or "").strip(),
        _MAX_PATH_CHARS,
    )
    crumbs = _format_crumbs(ctx.get("breadcrumbs"))
    meta = ctx.get("metadata") if isinstance(ctx.get("metadata"), dict) else {}

    focus_bits: List[str] = []
    app_label = _meta_label(meta, ("app_title", "app_name", "app"))
    track_label = _meta_label(meta, ("track_title", "track_name", "track"))
    entry_label = _meta_label(meta, ("entry_title", "entry_name", "entry"))
    if app_label:
        focus_bits.append(f'app="{app_label}"')
    app_id = _clip(str(ctx.get("focused_app_id") or "").strip(), 128)
    if app_id:
        focus_bits.append(f"app_id={app_id}")
    if track_label:
        focus_bits.append(f'track="{track_label}"')
    track_id = _clip(str(ctx.get("focused_track_id") or "").strip(), 128)
    if track_id:
        focus_bits.append(f"track_id={track_id}")
    view_id = _clip(str(ctx.get("focused_view_id") or "").strip(), 128)
    if view_id:
        focus_bits.append(f"view_id={view_id}")
    if entry_label:
        focus_bits.append(f'entry="{entry_label}"')
    entry_id = _clip(str(ctx.get("focused_entry_id") or "").strip(), 128)
    if entry_id:
        focus_bits.append(f"entry_id={entry_id}")
    dash_id = _clip(str(meta.get("focused_dashboard_id") or "").strip(), 128)
    if dash_id:
        focus_bits.append(f"dashboard_id={dash_id}")

    # Messenger embed fallback (title/path only).
    messenger_title = _clip(str(ctx.get("title") or "").strip(), _MAX_LABEL_CHARS)

    summary_bits: List[str] = []
    if kind:
        summary_bits.append(f"kind={kind}")
    summary_bits.extend(focus_bits)
    if not summary_bits and messenger_title:
        summary_bits.append(f'title="{messenger_title}"')
    if not summary_bits and not path and not crumbs:
        return None

    lines = [
        "UI ROUTE (optional focus — not default answer scope):",
    ]
    if summary_bits:
        lines.append("  " + " · ".join(summary_bits))
    if path:
        lines.append(f"  path={path}")
    if crumbs:
        lines.append(f"  crumbs={crumbs}")
    lines.append(
        "  Apply focused ids only when the user refers to the current screen "
        "(this/here/crumb name) or the ask clearly matches that resource; "
        "otherwise search the host workspace — do not answer from the focused "
        "resource merely because it is on screen."
    )
    return "\n".join(lines)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1] + "…"


def _meta_label(meta: Dict[str, Any], keys: tuple) -> str:
    for key in keys:
        raw = meta.get(key)
        if raw is None:
            continue
        text = _clip(str(raw).strip(), _MAX_LABEL_CHARS)
        if text:
            return text
    return ""


def _format_crumbs(raw: Any) -> str:
    if not isinstance(raw, list) or not raw:
        return ""
    labels: List[str] = []
    for item in raw[:12]:
        if isinstance(item, dict):
            label = str(item.get("label") or "").strip()
        else:
            label = str(item or "").strip()
        if label:
            labels.append(_clip(label, 40))
    if not labels:
        return ""
    return _clip(" › ".join(labels), _MAX_CRUMB_CHARS)


async def render_session_context(
    visitor: Any,
    *,
    app: Any = None,
) -> str:
    """Build the SESSION CONTEXT block for the turn's system prompt.

    Uses ``App.now()`` when an app is available; otherwise UTC wall clock.
    Channel is included when ``visitor.channel`` is set.
    Host UI route (ADR-0056) when ``visitor.data["page_context"]`` is set.
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
        ui_route = format_ui_route(data.get("page_context"))
        if ui_route:
            lines.append(ui_route)
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
