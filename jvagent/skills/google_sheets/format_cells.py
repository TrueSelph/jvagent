"""Format cells in a Google Sheets spreadsheet via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict, Optional


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "format_cells",
        "description": "Apply formatting to a range of cells in a Google Sheets spreadsheet.",
        "parameters": {
            "type": "object",
            "properties": {
                "spreadsheet_url_or_id": {
                    "type": "string",
                    "description": "Spreadsheet URL or ID (default: agent's configured spreadsheet)",
                },
                "range_name": {
                    "type": "string",
                    "description": "A1-style range to format",
                },
                "worksheet_title": {
                    "type": "string",
                    "description": "Worksheet title (default: first worksheet)",
                },
                "user_entered_format": {
                    "type": "object",
                    "description": "Formatting specification (e.g. background color, text format)",
                },
                "fields": {
                    "type": "string",
                    "description": "Field mask specifying which format fields to apply (e.g. 'userEnteredFormat')",
                },
            },
            "required": [],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    """Format cells by delegating to GoogleSheetsAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("GoogleSheetsAction")
    if action is None:
        return {"error": "GoogleSheetsAction not found on this agent"}

    return await action.format_cells(
        spreadsheet_url_or_id=arguments.get("spreadsheet_url_or_id"),
        range_name=arguments.get("range_name", ""),
        worksheet_title=arguments.get("worksheet_title"),
        user_entered_format=arguments.get("user_entered_format"),
        fields=arguments.get("fields"),
    )
