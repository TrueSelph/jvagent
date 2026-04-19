"""Update cells in a Google Sheets spreadsheet via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "update_spreadsheet",
        "description": "Update (overwrite) cells in a Google Sheets spreadsheet.",
        "parameters": {
            "type": "object",
            "properties": {
                "spreadsheet_url_or_id": {
                    "type": "string",
                    "description": "Spreadsheet URL or ID (default: agent's configured spreadsheet)",
                },
                "range_name": {
                    "type": "string",
                    "description": "A1-style range to update",
                },
                "values": {
                    "type": "array",
                    "items": {"type": "array"},
                    "description": "2D array of values to write",
                },
                "value_input_option": {
                    "type": "string",
                    "description": "How to interpret input data: 'RAW' or 'USER_ENTERED' (default: 'RAW')",
                },
                "worksheet_title": {
                    "type": "string",
                    "description": "Worksheet title (default: first worksheet)",
                },
            },
            "required": [],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    """Update spreadsheet cells by delegating to GoogleSheetsAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("GoogleSheetsAction")
    if action is None:
        return {"error": "GoogleSheetsAction not found on this agent"}

    return await action.update_spreadsheet(
        spreadsheet_url_or_id=arguments.get("spreadsheet_url_or_id"),
        range_name=arguments.get("range_name", ""),
        values=arguments.get("values"),
        value_input_option=arguments.get("value_input_option", "RAW"),
        worksheet_title=arguments.get("worksheet_title"),
    )
