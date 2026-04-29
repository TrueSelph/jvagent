"""Delete a Google Sheets spreadsheet via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "delete_spreadsheet",
        "description": "Permanently delete a Google Sheets spreadsheet.",
        "parameters": {
            "type": "object",
            "properties": {
                "spreadsheet_url_or_id": {
                    "type": "string",
                    "description": "Spreadsheet URL or ID",
                },
            },
            "required": [],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    """Delete a spreadsheet by delegating to GoogleSheetsAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("GoogleSheetsAction")
    if action is None:
        return {"error": "GoogleSheetsAction not found on this agent"}

    result = await action.delete_spreadsheet(
        spreadsheet_url_or_id=arguments.get("spreadsheet_url_or_id"),
    )
    return {"deleted": result}
