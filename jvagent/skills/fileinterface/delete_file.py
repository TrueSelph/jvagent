"""Delete a file in the user sandbox."""

from __future__ import annotations

from typing import Any, Dict

from jvagent.skills.fileinterface import _core
from jvagent.skills.fileinterface._tool_protocol_ref import OTHER_TOOLS


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "delete_file",
        "description": (
            "Delete a file at a path relative to the user sandbox." + OTHER_TOOLS
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative file path.",
                },
            },
            "required": ["path"],
        },
    }


async def execute(arguments: dict, *, visitor: Any) -> Any:
    path = str(arguments.get("path") or "").strip()
    try:
        ok = await _core.delete_file(visitor, path)
        return {"ok": ok, "path": path}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
