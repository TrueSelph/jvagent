"""Share a OneDrive file via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict, Optional


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "share_file",
        "description": "Share a OneDrive file.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_id": {
                    "type": "string",
                    "description": "The ID of the file to share",
                },
                "share_type": {
                    "type": "string",
                    "description": "Type of sharing: 'link' or 'user' (default: 'link')",
                },
                "link_scope": {
                    "type": "string",
                    "description": "Link scope: 'anyone' or 'organization' (default: 'anyone')",
                },
                "email": {
                    "type": "string",
                    "description": "Email address for user-level sharing",
                },
                "role": {
                    "type": "string",
                    "description": "Permission role: 'read' or 'write' (default: 'read')",
                },
            },
            "required": ["file_id"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Any:
    """Share a OneDrive file by delegating to MicrosoftOneDriveAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("MicrosoftOneDriveAction")
    if action is None:
        return {"error": "MicrosoftOneDriveAction not found on this agent"}

    return await action.share_file(
        file_id=arguments["file_id"],
        share_type=arguments.get("share_type", "link"),
        link_scope=arguments.get("link_scope", "anyone"),
        email=arguments.get("email"),
        role=arguments.get("role", "read"),
    )
