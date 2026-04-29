"""List files under a path prefix in the user sandbox."""

from __future__ import annotations

from typing import Any, Dict

from jvagent.skills.fileinterface import _core
from jvagent.skills.fileinterface.scripts._tool_protocol_ref import OTHER_TOOLS


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "list_directory",
        "description": (
            "List immediate files and subdirectories under a relative path in the user sandbox. "
            "Use path '' for the workspace root." + OTHER_TOOLS
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path relative to sandbox (omit or '' for sandbox root).",
                    "default": "",
                },
            },
        },
    }


async def execute(arguments: dict, *, visitor: Any) -> Any:
    path = str(arguments.get("path", "")).strip()
    try:
        listing = await _core.list_directory(visitor, path)
        return {"ok": True, "path": path or ".", "listing": listing}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
