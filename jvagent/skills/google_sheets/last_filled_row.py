"""Get last filled row in a Google Sheets column via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "last_filled_row",
        "description": "Get the 1-based row number of the last filled cell in a column of a Google Sheets spreadsheet.",
        "parameters": {
            "type": "object",
            "properties": {
                "spreadsheet_url_or_id": {
                    "type": "string",
                    "description": "Spreadsheet URL or ID (default: agent's configured spreadsheet)",
                },
                "column": {
                    "type": "string",
                    "description": "Column letter to check (default: 'A')",
                },
                "worksheet_title": {
                    "type": "string",
                    "description": "Worksheet title (default: first worksheet)",
                },
            },
            "required": [],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Any:
    """Get last filled row by delegating to GoogleSheetsAction.last_filled_row_1based."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("GoogleSheetsAction")
    if action is None:
        return {"error": "GoogleSheetsAction not found on this agent"}

    return await action.last_filled_row_1based(
        spreadsheet_url_or_id=arguments.get("spreadsheet_url_or_id"),
        column=arguments.get("column", "A"),
        worksheet_title=arguments.get("worksheet_title"),
    )
