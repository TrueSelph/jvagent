"""Update a worksheet in a Google Sheets spreadsheet via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict, Optional


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "update_worksheet",
        "description": "Update properties of a worksheet in a Google Sheets spreadsheet.",
        "parameters": {
            "type": "object",
            "properties": {
                "worksheet_title": {
                    "type": "string",
                    "description": "Title of the worksheet to update",
                },
                "spreadsheet_url_or_id": {
                    "type": "string",
                    "description": "Spreadsheet URL or ID (default: agent's configured spreadsheet)",
                },
                "new_title": {
                    "type": "string",
                    "description": "New title for the worksheet",
                },
                "rows": {
                    "type": "integer",
                    "description": "New number of rows",
                },
                "cols": {
                    "type": "integer",
                    "description": "New number of columns",
                },
                "hidden": {
                    "type": "boolean",
                    "description": "Whether the worksheet is hidden",
                },
                "tab_color": {
                    "type": "string",
                    "description": "Tab color as a hex string (e.g. '#FF0000')",
                },
            },
            "required": ["worksheet_title"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    """Update a worksheet by delegating to GoogleSheetsAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("GoogleSheetsAction")
    if action is None:
        return {"error": "GoogleSheetsAction not found on this agent"}

    return await action.update_worksheet(
        worksheet_title=arguments["worksheet_title"],
        spreadsheet_url_or_id=arguments.get("spreadsheet_url_or_id"),
        new_title=arguments.get("new_title"),
        rows=arguments.get("rows"),
        cols=arguments.get("cols"),
        hidden=arguments.get("hidden"),
        tab_color=arguments.get("tab_color"),
    )
