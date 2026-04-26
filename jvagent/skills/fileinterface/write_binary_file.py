"""Write binary content (base64) under the agent/user sandbox."""

from __future__ import annotations

import base64
from typing import Any, Dict

from jvagent.skills.fileinterface import _core
from jvagent.skills.fileinterface._tool_protocol_ref import OTHER_TOOLS


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "write_binary_file",
        "description": (
            "Write binary data to a path in the user sandbox. "
            "Pass content as base64-encoded ASCII (standard for JSON tools)."
            + OTHER_TOOLS
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path (e.g. output/report.pdf).",
                },
                "content_b64": {
                    "type": "string",
                    "description": "File bytes as a standard base64 string.",
                },
            },
            "required": ["path", "content_b64"],
        },
    }


async def execute(arguments: dict, *, visitor: Any) -> Any:
    path = str(arguments.get("path") or "").strip()
    b64 = str(arguments.get("content_b64") or "")
    try:
        data = base64.b64decode(b64)
    except Exception as e:
        return {"ok": False, "error": f"base64 decode failed: {e}"}
    try:
        await _core.write_binary_file(visitor, path, data)
        return {"ok": True, "path": path, "size": len(data)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
