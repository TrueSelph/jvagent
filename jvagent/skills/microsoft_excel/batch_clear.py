"""Clear ranges in an Excel spreadsheet via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict, List


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "batch_clear",
        "description": "Clear one or more ranges of values in an Excel spreadsheet.",
        "parameters": {
            "type": "object",
            "properties": {
                "spreadsheet_url_or_id": {
                    "type": "string",
                    "description": "Spreadsheet URL or ID",
                },
                "ranges": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of A1 notation ranges to clear",
                },
                "worksheet_title": {
                    "type": "string",
                    "description": "Worksheet title to clear ranges from",
                },
            },
            "required": [],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    """Clear spreadsheet ranges by delegating to MicrosoftExcelAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("MicrosoftExcelAction")
    if action is None:
        return {"error": "MicrosoftExcelAction not found on this agent"}

    return await action.batch_clear(
        spreadsheet_url_or_id=arguments.get("spreadsheet_url_or_id"),
        ranges=arguments.get("ranges"),
        worksheet_title=arguments.get("worksheet_title"),
    )
