"""Append data to an Excel spreadsheet via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "append_spreadsheet",
        "description": "Append rows of data after the last row of data in an Excel spreadsheet.",
        "parameters": {
            "type": "object",
            "properties": {
                "spreadsheet_url_or_id": {
                    "type": "string",
                    "description": "Spreadsheet URL or ID",
                },
                "range_name": {
                    "type": "string",
                    "description": "A1 notation range to append to (determines the table). Default: None",
                },
                "values": {
                    "type": "array",
                    "items": {"type": "array", "items": {}},
                    "description": "2D array of values to append",
                },
                "value_input_option": {
                    "type": "string",
                    "description": "How to interpret input values: 'RAW' or 'USER_ENTERED'. Default: 'RAW'",
                },
                "worksheet_title": {
                    "type": "string",
                    "description": "Worksheet title to append to",
                },
            },
            "required": [],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    """Append spreadsheet data by delegating to MicrosoftExcelAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("MicrosoftExcelAction")
    if action is None:
        return {"error": "MicrosoftExcelAction not found on this agent"}

    return await action.append_spreadsheet(
        spreadsheet_url_or_id=arguments.get("spreadsheet_url_or_id"),
        range_name=arguments.get("range_name"),
        values=arguments.get("values"),
        value_input_option=arguments.get("value_input_option", "RAW"),
        worksheet_title=arguments.get("worksheet_title"),
    )
