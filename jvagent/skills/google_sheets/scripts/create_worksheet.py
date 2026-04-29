"""Create a worksheet in a Google Sheets spreadsheet via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "create_worksheet",
        "description": "Create a new worksheet in a Google Sheets spreadsheet.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Title for the new worksheet",
                },
                "spreadsheet_url_or_id": {
                    "type": "string",
                    "description": "Spreadsheet URL or ID (default: agent's configured spreadsheet)",
                },
                "rows": {
                    "type": "integer",
                    "description": "Number of rows for the new worksheet (default: 1000)",
                },
                "cols": {
                    "type": "integer",
                    "description": "Number of columns for the new worksheet (default: 26)",
                },
            },
            "required": ["title"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    """Create a worksheet by delegating to GoogleSheetsAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("GoogleSheetsAction")
    if action is None:
        return {"error": "GoogleSheetsAction not found on this agent"}

    return await action.create_worksheet(
        title=arguments["title"],
        spreadsheet_url_or_id=arguments.get("spreadsheet_url_or_id"),
        rows=arguments.get("rows", 1000),
        cols=arguments.get("cols", 26),
    )
