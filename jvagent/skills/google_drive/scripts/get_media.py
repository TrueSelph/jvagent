"""Download Google Drive file media via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "get_media",
        "description": "Download a file's media content from Google Drive.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_id": {
                    "type": "string",
                    "description": "The ID of the file to download",
                },
            },
            "required": ["file_id"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Any:
    """Download Drive file media by delegating to GoogleDriveAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("GoogleDriveAction")
    if action is None:
        return {"error": "GoogleDriveAction not found on this agent"}

    return await action.get_media(file_id=arguments["file_id"])
