"""Write UTF-8 text to a path under the agent/user sandbox."""

from __future__ import annotations

from typing import Any, Dict

from jvagent.skills.fileinterface import _core
from jvagent.skills.fileinterface.scripts._tool_protocol_ref import OTHER_TOOLS


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "write_file",
        "description": (
            "Create or overwrite a file with UTF-8 text in the current user's sandbox "
            "(jvspatial storage). Parent path segments are created as needed."
            + OTHER_TOOLS
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path (e.g. output/notes.md).",
                },
                "content": {
                    "type": "string",
                    "description": "Full file content as UTF-8 text.",
                },
            },
            "required": ["path", "content"],
        },
    }


async def execute(arguments: dict, *, visitor: Any) -> Any:
    path = str(arguments.get("path") or "").strip()
    content = str(arguments.get("content") or "")
    try:
        await _core.write_text_file(visitor, path, content)
        return {"ok": True, "path": path, "bytes": len(content.encode("utf-8"))}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
