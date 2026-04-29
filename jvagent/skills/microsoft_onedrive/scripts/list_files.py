"""List OneDrive files via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict, List


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "list_files",
        "description": "List files in a OneDrive folder.",
        "parameters": {
            "type": "object",
            "properties": {
                "folder_id": {
                    "type": "string",
                    "description": "ID of the folder to list (root if omitted)",
                },
                "with_link": {
                    "type": "boolean",
                    "description": "Include sharing links (default: false)",
                },
                "depth": {
                    "type": "integer",
                    "description": "Recursion depth for subfolders (default: 5)",
                },
            },
            "required": [],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> List[Dict[str, Any]]:
    """List OneDrive files by delegating to MicrosoftOneDriveAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return [{"error": "ActionResolver not available"}]

    action = await resolver.resolve("MicrosoftOneDriveAction")
    if action is None:
        return [{"error": "MicrosoftOneDriveAction not found on this agent"}]

    return await action.list_files(
        folder_id=arguments.get("folder_id"),
        with_link=arguments.get("with_link", False),
        depth=arguments.get("depth", 5),
    )
