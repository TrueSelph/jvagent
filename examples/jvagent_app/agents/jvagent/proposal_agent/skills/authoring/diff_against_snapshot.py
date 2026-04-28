"""Diff current proposal content against a named snapshot."""

from __future__ import annotations

import difflib
from typing import Any, Dict


def _snapshots(visitor: Any) -> Dict[str, Any]:
    state = getattr(visitor, "_skill_state", None) or {}
    return state.get("authoring_snapshots", {})


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "authoring__diff_against_snapshot",
        "description": "Compare current content with a previously captured snapshot.",
        "parameters": {
            "type": "object",
            "properties": {
                "snapshot_name": {"type": "string"},
                "current_content": {"type": "string"},
            },
            "required": ["snapshot_name", "current_content"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    snapshot_name = arguments.get("snapshot_name")
    current_content = arguments.get("current_content", "")
    snapshots = _snapshots(visitor)
    snapshot = snapshots.get(snapshot_name)
    if snapshot is None:
        return {
            "status": "missing_snapshot",
            "snapshot_name": snapshot_name,
            "message": "No snapshot exists with that name.",
        }

    original = snapshot.get("content", "")
    diff_lines = list(
        difflib.unified_diff(
            original.splitlines(),
            current_content.splitlines(),
            fromfile=f"{snapshot_name}:baseline",
            tofile="current",
            lineterm="",
        )
    )
    return {
        "status": "ok",
        "snapshot_name": snapshot_name,
        "changed": bool(diff_lines),
        "diff": "\n".join(diff_lines),
    }
