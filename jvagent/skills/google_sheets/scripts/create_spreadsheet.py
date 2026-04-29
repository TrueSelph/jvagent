"""Create a Google Sheets spreadsheet via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "create_spreadsheet",
        "description": "Create a new Google Sheets spreadsheet.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Title for the new spreadsheet",
                },
            },
            "required": ["title"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    """Create a spreadsheet by delegating to GoogleSheetsAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("GoogleSheetsAction")
    if action is None:
        return {"error": "GoogleSheetsAction not found on this agent"}

    return await action.create_spreadsheet(
        title=arguments["title"],
    )
