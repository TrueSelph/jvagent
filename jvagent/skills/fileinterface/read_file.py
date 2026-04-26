"""Read a UTF-8 text file under the agent/user sandbox."""

from __future__ import annotations

from typing import Any, Dict, Optional

from jvagent.skills.fileinterface import _core
from jvagent.skills.fileinterface._tool_protocol_ref import OTHER_TOOLS


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "read_file",
        "description": (
            "Read a text file from the current agent's user-scoped storage "
            "(jvspatial file interface: local or S3). Path is relative to the sandbox root. "
            "Optional head/tail limit lines returned." + OTHER_TOOLS
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path under the user sandbox (e.g. output/doc.md).",
                },
                "head": {
                    "type": "integer",
                    "description": "If set, return only the first N lines.",
                },
                "tail": {
                    "type": "integer",
                    "description": "If set, return only the last N lines.",
                },
            },
            "required": ["path"],
        },
    }


async def execute(arguments: dict, *, visitor: Any) -> Any:
    path = str(arguments.get("path") or "").strip()
    head: Optional[int] = arguments.get("head")
    tail: Optional[int] = arguments.get("tail")
    if head is not None:
        try:
            head = int(head)
        except (TypeError, ValueError):
            head = None
    if tail is not None:
        try:
            tail = int(tail)
        except (TypeError, ValueError):
            tail = None
    try:
        text = await _core.read_text_file(visitor, path, head=head, tail=tail)
        return {"ok": True, "path": path, "content": text}
    except FileNotFoundError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
