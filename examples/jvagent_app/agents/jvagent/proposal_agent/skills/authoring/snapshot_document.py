"""Capture a snapshot of the current review artifact."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Dict


def _state(visitor: Any) -> Dict[str, Any]:
    state = getattr(visitor, "_skill_state", None)
    if state is None:
        state = {}
        setattr(visitor, "_skill_state", state)
    snapshots = state.setdefault("authoring_snapshots", {})
    return snapshots


def _hash(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "authoring__snapshot_document",
        "description": "Create and store a named snapshot of proposal text for later diffing.",
        "parameters": {
            "type": "object",
            "properties": {
                "snapshot_name": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["snapshot_name", "content"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    snapshot_name = arguments.get("snapshot_name", "latest")
    content = arguments.get("content", "")
    snapshots = _state(visitor)
    digest = _hash(content)
    snapshots[snapshot_name] = {
        "content": content,
        "hash": digest,
        "captured_at": datetime.utcnow().isoformat(),
    }
    return {
        "snapshot_name": snapshot_name,
        "hash": digest,
        "captured_at": snapshots[snapshot_name]["captured_at"],
    }
