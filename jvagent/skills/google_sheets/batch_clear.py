"""Batch clear ranges in a Google Sheets spreadsheet via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "batch_clear",
        "description": "Clear one or more ranges of cells in a Google Sheets spreadsheet.",
        "parameters": {
            "type": "object",
            "properties": {
                "spreadsheet_url_or_id": {
                    "type": "string",
                    "description": "Spreadsheet URL or ID (default: agent's configured spreadsheet)",
                },
                "ranges": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of A1-style ranges to clear",
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
    """Batch clear ranges by delegating to GoogleSheetsAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("GoogleSheetsAction")
    if action is None:
        return {"error": "GoogleSheetsAction not found on this agent"}

    return await action.batch_clear(
        spreadsheet_url_or_id=arguments.get("spreadsheet_url_or_id"),
        ranges=arguments.get("ranges"),
        worksheet_title=arguments.get("worksheet_title"),
    )
