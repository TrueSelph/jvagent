"""Scaffold empty profile files for `jvagent app profile new`."""

from __future__ import annotations

from pathlib import Path
from typing import Optional


def write_profile_stub(
    app_root: Path, name: str, *, extends: Optional[str] = None
) -> None:
    """Write ``profiles/<name>.yaml`` with optional ``extends``."""
    safe = name.strip().replace("/", "_").replace("..", "")
    if not safe:
        raise ValueError("Invalid profile name")

    profiles = app_root / "profiles"
    profiles.mkdir(parents=True, exist_ok=True)
    path = profiles / f"{safe}.yaml"
    if path.exists():
        raise FileExistsError(f"Profile already exists: {path}")

    lines = [
        f"# Custom profile: {safe}",
        "# Used with: jvagent agent create ns/agent@" + safe,
        "",
    ]
    if extends:
        lines.append(f"extends: {extends}")
        lines.append("")
    lines.append("actions: []")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
