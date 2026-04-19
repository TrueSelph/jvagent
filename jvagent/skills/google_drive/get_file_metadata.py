"""Get Google Drive file metadata via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "get_file_metadata",
        "description": "Get metadata for a Google Drive file.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_id": {
                    "type": "string",
                    "description": "The ID of the file",
                },
                "fields": {
                    "type": "string",
                    "description": "Comma-separated list of fields to return (default: 'id, name, mimeType')",
                },
            },
            "required": ["file_id"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Any:
    """Get file metadata by delegating to GoogleDriveAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("GoogleDriveAction")
    if action is None:
        return {"error": "GoogleDriveAction not found on this agent"}

    return await action.get_file_metadata(
        file_id=arguments["file_id"],
        fields=arguments.get("fields", "id, name, mimeType"),
    )
