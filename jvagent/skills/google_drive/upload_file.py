"""Upload a file to Google Drive via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict, Optional


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "upload_file",
        "description": "Upload a file to Google Drive.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name for the uploaded file",
                },
                "content": {
                    "type": "string",
                    "description": "Text content to upload (use this or source_url)",
                },
                "source_url": {
                    "type": "string",
                    "description": "URL to download file content from (use this or content)",
                },
                "mime_type": {
                    "type": "string",
                    "description": "MIME type of the file",
                },
                "parent_folder_id": {
                    "type": "string",
                    "description": "ID of the parent folder",
                },
            },
            "required": ["name"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Any:
    """Upload a file by delegating to GoogleDriveAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("GoogleDriveAction")
    if action is None:
        return {"error": "GoogleDriveAction not found on this agent"}

    return await action.upload_file(
        name=arguments["name"],
        content=arguments.get("content"),
        source_url=arguments.get("source_url"),
        mime_type=arguments.get("mime_type"),
        parent_folder_id=arguments.get("parent_folder_id"),
    )
