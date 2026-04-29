"""Merge cells in a Google Sheets spreadsheet via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "merge_cells",
        "description": "Merge a range of cells in a Google Sheets spreadsheet.",
        "parameters": {
            "type": "object",
            "properties": {
                "spreadsheet_url_or_id": {
                    "type": "string",
                    "description": "Spreadsheet URL or ID (default: agent's configured spreadsheet)",
                },
                "range_name": {
                    "type": "string",
                    "description": "A1-style range to merge",
                },
                "worksheet_title": {
                    "type": "string",
                    "description": "Worksheet title (default: first worksheet)",
                },
                "merge_type": {
                    "type": "string",
                    "description": "Merge type: 'MERGE_ALL' or 'MERGE_ROWS' or 'MERGE_COLUMNS' (default: 'MERGE_ALL')",
                },
            },
            "required": [],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    """Merge cells by delegating to GoogleSheetsAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("GoogleSheetsAction")
    if action is None:
        return {"error": "GoogleSheetsAction not found on this agent"}

    return await action.merge_cells(
        spreadsheet_url_or_id=arguments.get("spreadsheet_url_or_id"),
        range_name=arguments.get("range_name", ""),
        worksheet_title=arguments.get("worksheet_title"),
        merge_type=arguments.get("merge_type", "MERGE_ALL"),
    )
