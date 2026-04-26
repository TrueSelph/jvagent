"""Ensure a directory exists in the user sandbox."""

from __future__ import annotations

from typing import Any, Dict

from jvagent.skills.fileinterface import _core
from jvagent.skills.fileinterface._tool_protocol_ref import OTHER_TOOLS


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "create_directory",
        "description": (
            "Create a directory (or ensure it exists) under the user sandbox."
            + OTHER_TOOLS
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative directory path.",
                },
            },
            "required": ["path"],
        },
    }


async def execute(arguments: dict, *, visitor: Any) -> Any:
    path = str(arguments.get("path") or "").strip()
    try:
        await _core.create_directory(visitor, path)
        return {"ok": True, "path": path}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
