"""Expose sandbox layout and suggested write prefixes before creating files."""

from __future__ import annotations

from typing import Any, Dict

from jvagent.skills.fileinterface import _core


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "describe_write_workspace",
        "description": (
            "First step for fileinterface work in a task (see bundled fileinterface skill). "
            "Returns top-level directories and files, recommended relative path prefixes, and "
            "what is writable under the user sandbox. Does not read or write user file content."
        ),
        "parameters": {"type": "object", "properties": {}},
    }


async def execute(arguments: dict, *, visitor: Any) -> Any:
    try:
        data = await _core.describe_write_workspace(visitor)
        return {"ok": True, **data}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
