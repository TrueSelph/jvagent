"""Read data from an Excel spreadsheet via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict, List


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "read_spreadsheet",
        "description": "Read data from an Excel spreadsheet.",
        "parameters": {
            "type": "object",
            "properties": {
                "spreadsheet_url_or_id": {
                    "type": "string",
                    "description": "Spreadsheet URL or ID",
                },
                "range_name": {
                    "type": "string",
                    "description": "A1 notation range (e.g. 'A1:D10'). Default: empty (reads entire worksheet)",
                },
                "worksheet_title": {
                    "type": "string",
                    "description": "Worksheet title to read from",
                },
            },
            "required": [],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> List[List[Any]]:
    """Read spreadsheet data by delegating to MicrosoftExcelAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return [["error", "ActionResolver not available"]]

    action = await resolver.resolve("MicrosoftExcelAction")
    if action is None:
        return [["error", "MicrosoftExcelAction not found on this agent"]]

    return await action.read_spreadsheet(
        spreadsheet_url_or_id=arguments.get("spreadsheet_url_or_id"),
        range_name=arguments.get("range_name", ""),
        worksheet_title=arguments.get("worksheet_title"),
    )
