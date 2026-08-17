"""Vendor-neutral spreadsheet A1 range helpers (Google Sheets + Excel)."""

import re
from typing import Optional, Sequence

_SPREADSHEET_URL_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")


def resolve_spreadsheet_id(spreadsheet_url_or_id: str) -> str:
    """Extract spreadsheet id from a Google Sheets URL or raw id string."""
    s = spreadsheet_url_or_id.strip()
    if "docs.google.com/spreadsheets/d/" in s:
        m = _SPREADSHEET_URL_RE.search(s)
        if not m:
            raise ValueError(f"Could not parse spreadsheet id from URL: {s!r}")
        return m.group(1)
    return s


def normalize_sheet_title(title: str) -> str:
    """Compare tab names ignoring case and treating ``_`` as space."""
    return (title or "").casefold().replace("_", " ").strip()


def resolve_sheet_title(wanted: str, tabs: Sequence[str]) -> Optional[str]:
    """Return the spreadsheet tab that matches ``wanted``, or ``None``.

    Exact match wins. Otherwise a unique normalized match (casefold, ``_`` → space).
    """
    wanted = (wanted or "").strip()
    if not wanted:
        return None
    titles = [str(t) for t in tabs if str(t).strip()]
    if wanted in titles:
        return wanted
    key = normalize_sheet_title(wanted)
    matches = [t for t in titles if normalize_sheet_title(t) == key]
    if len(matches) == 1:
        return matches[0]
    return None


def qualify_sheet_title(title: str) -> str:
    """Return an A1-safe sheet title token (quote when required by Sheets rules).

    Unquoted names with letters+digits (e.g. ``FAB_2026``) are parsed as A1, not
    a tab name. Quote whenever the title has a digit, space, apostrophe, or
    other non-alnum/underscore character.
    """
    if not title:
        return title
    needs_quote = (
        " " in title
        or "'" in title
        or any(c.isdigit() for c in title)
        or any(not (c.isalnum() or c == "_") for c in title)
        or title[0].isdigit()
    )
    if needs_quote:
        return "'" + title.replace("'", "''") + "'"
    return title


def compose_a1_range(
    worksheet_title: str,
    range_name: Optional[str],
) -> str:
    """Build a single range string for spreadsheet read/write APIs."""
    if range_name and "!" in range_name:
        return range_name
    qt = qualify_sheet_title(worksheet_title)
    if not range_name:
        return f"{qt}!A:ZZ"
    return f"{qt}!{range_name}"
