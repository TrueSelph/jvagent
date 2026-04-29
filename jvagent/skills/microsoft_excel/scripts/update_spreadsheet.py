"""Update data in an Excel spreadsheet via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "update_spreadsheet",
        "description": "Update (overwrite) values in an Excel spreadsheet range.",
        "parameters": {
            "type": "object",
            "properties": {
                "spreadsheet_url_or_id": {
                    "type": "string",
                    "description": "Spreadsheet URL or ID",
                },
                "range_name": {
                    "type": "string",
                    "description": "A1 notation range (e.g. 'A1:D10'). Default: empty",
                },
                "values": {
                    "type": "array",
                    "items": {"type": "array", "items": {}},
                    "description": "2D array of values to write",
                },
                "value_input_option": {
                    "type": "string",
                    "description": "How to interpret input values: 'RAW' or 'USER_ENTERED'. Default: 'RAW'",
                },
                "worksheet_title": {
                    "type": "string",
                    "description": "Worksheet title to update",
                },
            },
            "required": [],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    """Update spreadsheet data by delegating to MicrosoftExcelAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("MicrosoftExcelAction")
    if action is None:
        return {"error": "MicrosoftExcelAction not found on this agent"}

    return await action.update_spreadsheet(
        spreadsheet_url_or_id=arguments.get("spreadsheet_url_or_id"),
        range_name=arguments.get("range_name", ""),
        values=arguments.get("values"),
        value_input_option=arguments.get("value_input_option", "RAW"),
        worksheet_title=arguments.get("worksheet_title"),
    )
