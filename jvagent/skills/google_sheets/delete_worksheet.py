"""Delete a worksheet from a Google Sheets spreadsheet via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "delete_worksheet",
        "description": "Delete a worksheet from a Google Sheets spreadsheet.",
        "parameters": {
            "type": "object",
            "properties": {
                "worksheet_title": {
                    "type": "string",
                    "description": "Title of the worksheet to delete",
                },
                "spreadsheet_url_or_id": {
                    "type": "string",
                    "description": "Spreadsheet URL or ID (default: agent's configured spreadsheet)",
                },
            },
            "required": ["worksheet_title"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    """Delete a worksheet by delegating to GoogleSheetsAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("GoogleSheetsAction")
    if action is None:
        return {"error": "GoogleSheetsAction not found on this agent"}

    return await action.delete_worksheet(
        worksheet_title=arguments["worksheet_title"],
        spreadsheet_url_or_id=arguments.get("spreadsheet_url_or_id"),
    )
