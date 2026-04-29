"""Create a new worksheet in an Excel spreadsheet via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "create_worksheet",
        "description": "Create a new worksheet in an Excel spreadsheet.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Title for the new worksheet",
                },
                "spreadsheet_url_or_id": {
                    "type": "string",
                    "description": "Spreadsheet URL or ID to add the worksheet to",
                },
                "rows": {
                    "type": "integer",
                    "description": "Number of rows for the new worksheet. Default: 1000",
                },
                "cols": {
                    "type": "integer",
                    "description": "Number of columns for the new worksheet. Default: 26",
                },
            },
            "required": ["title"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    """Create a worksheet by delegating to MicrosoftExcelAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("MicrosoftExcelAction")
    if action is None:
        return {"error": "MicrosoftExcelAction not found on this agent"}

    return await action.create_worksheet(
        title=arguments["title"],
        spreadsheet_url_or_id=arguments.get("spreadsheet_url_or_id"),
        rows=arguments.get("rows", 1000),
        cols=arguments.get("cols", 26),
    )
