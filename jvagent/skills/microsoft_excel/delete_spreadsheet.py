"""Delete an Excel spreadsheet via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "delete_spreadsheet",
        "description": "Delete an entire Excel spreadsheet (workbook).",
        "parameters": {
            "type": "object",
            "properties": {
                "spreadsheet_url_or_id": {
                    "type": "string",
                    "description": "Spreadsheet URL or ID to delete",
                },
            },
            "required": [],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> bool:
    """Delete a spreadsheet by delegating to MicrosoftExcelAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return False

    action = await resolver.resolve("MicrosoftExcelAction")
    if action is None:
        return False

    return await action.delete_spreadsheet(
        spreadsheet_url_or_id=arguments.get("spreadsheet_url_or_id"),
    )
