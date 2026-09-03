"""Validate user-supplied path segments before filesystem writes."""

from __future__ import annotations

import re
from pathlib import Path

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def validate_safe_segment(name: str, *, label: str = "name") -> str:
    """Reject traversal, separators, and empty segments."""
    value = (name or "").strip()
    if not value:
        raise ValueError(f"Invalid {label}: empty")
    if value in {".", ".."} or ".." in value:
        raise ValueError(f"Invalid {label}: {value!r}")
    if "/" in value or "\\" in value or value.startswith("."):
        raise ValueError(f"Invalid {label}: {value!r}")
    if not _SAFE_SEGMENT.match(value):
        raise ValueError(f"Invalid {label}: {value!r}")
    return value


def resolve_under(base: Path, *parts: str) -> Path:
    """Resolve *parts* under *base*; raise if the result escapes *base*."""
    root = base.resolve()
    dest = root.joinpath(*parts).resolve()
    try:
        dest.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path escapes base {root}: {dest}") from exc
    return dest
