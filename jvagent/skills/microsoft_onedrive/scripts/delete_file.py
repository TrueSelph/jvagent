"""Delete a OneDrive file via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "delete_file",
        "description": "Delete a file from OneDrive.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_id": {
                    "type": "string",
                    "description": "The ID of the file to delete",
                },
            },
            "required": ["file_id"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Any:
    """Delete a OneDrive file by delegating to MicrosoftOneDriveAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("MicrosoftOneDriveAction")
    if action is None:
        return {"error": "MicrosoftOneDriveAction not found on this agent"}

    result = await action.delete_file(file_id=arguments["file_id"])
    return {"deleted": result}
