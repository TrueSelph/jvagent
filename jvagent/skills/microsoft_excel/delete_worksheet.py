"""Delete a worksheet from an Excel spreadsheet via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "delete_worksheet",
        "description": "Delete a worksheet from an Excel spreadsheet.",
        "parameters": {
            "type": "object",
            "properties": {
                "worksheet_title": {
                    "type": "string",
                    "description": "Title of the worksheet to delete",
                },
                "spreadsheet_url_or_id": {
                    "type": "string",
                    "description": "Spreadsheet URL or ID",
                },
            },
            "required": ["worksheet_title"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    """Delete a worksheet by delegating to MicrosoftExcelAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("MicrosoftExcelAction")
    if action is None:
        return {"error": "MicrosoftExcelAction not found on this agent"}

    return await action.delete_worksheet(
        worksheet_title=arguments["worksheet_title"],
        spreadsheet_url_or_id=arguments.get("spreadsheet_url_or_id"),
    )
