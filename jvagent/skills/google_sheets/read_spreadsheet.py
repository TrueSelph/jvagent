"""Read Google Sheets spreadsheet via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict, List


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "read_spreadsheet",
        "description": "Read data from a Google Sheets spreadsheet.",
        "parameters": {
            "type": "object",
            "properties": {
                "spreadsheet_url_or_id": {
                    "type": "string",
                    "description": "Spreadsheet URL or ID (default: agent's configured spreadsheet)",
                },
                "range_name": {
                    "type": "string",
                    "description": "A1-style range to read (default: entire worksheet)",
                },
                "worksheet_title": {
                    "type": "string",
                    "description": "Worksheet title (default: first worksheet)",
                },
            },
            "required": [],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> List[List[Any]]:
    """Read spreadsheet data by delegating to GoogleSheetsAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return [["error", "ActionResolver not available"]]

    action = await resolver.resolve("GoogleSheetsAction")
    if action is None:
        return [["error", "GoogleSheetsAction not found on this agent"]]

    return await action.read_spreadsheet(
        spreadsheet_url_or_id=arguments.get("spreadsheet_url_or_id"),
        range_name=arguments.get("range_name", ""),
        worksheet_title=arguments.get("worksheet_title"),
    )
