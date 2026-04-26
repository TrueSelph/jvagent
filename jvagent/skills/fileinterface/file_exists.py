"""Check whether a path exists in the user sandbox."""

from __future__ import annotations

from typing import Any, Dict

from jvagent.skills.fileinterface import _core
from jvagent.skills.fileinterface._tool_protocol_ref import OTHER_TOOLS


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "file_exists",
        "description": (
            "Return whether a file or marker exists at the given relative path."
            + OTHER_TOOLS
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path.",
                },
            },
            "required": ["path"],
        },
    }


async def execute(arguments: dict, *, visitor: Any) -> Any:
    path = str(arguments.get("path") or "").strip()
    try:
        exists = await _core.file_exists(visitor, path)
        return {"ok": True, "path": path, "exists": exists}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
