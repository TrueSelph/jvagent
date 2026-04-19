"""Share an Excel spreadsheet via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict, Optional


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "share_spreadsheet",
        "description": "Share an Excel spreadsheet via link or with a specific email.",
        "parameters": {
            "type": "object",
            "properties": {
                "spreadsheet_url_or_id": {
                    "type": "string",
                    "description": "Spreadsheet URL or ID",
                },
                "share_type": {
                    "type": "string",
                    "description": "Type of sharing: 'link' or 'email'. Default: 'link'",
                },
                "link_scope": {
                    "type": "string",
                    "description": "Link scope when share_type is 'link': 'anyone' or 'domain'. Default: 'anyone'",
                },
                "email": {
                    "type": "string",
                    "description": "Email address when share_type is 'email'",
                },
                "role": {
                    "type": "string",
                    "description": "Role to assign: 'reader', 'writer', or 'owner'. Default: 'reader'",
                },
            },
            "required": [],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    """Share a spreadsheet by delegating to MicrosoftExcelAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("MicrosoftExcelAction")
    if action is None:
        return {"error": "MicrosoftExcelAction not found on this agent"}

    return await action.share_spreadsheet(
        spreadsheet_url_or_id=arguments.get("spreadsheet_url_or_id"),
        share_type=arguments.get("share_type", "link"),
        link_scope=arguments.get("link_scope", "anyone"),
        email=arguments.get("email"),
        role=arguments.get("role", "reader"),
    )
