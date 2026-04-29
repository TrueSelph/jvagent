"""Share a Google Sheets spreadsheet via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "share_spreadsheet",
        "description": "Share a Google Sheets spreadsheet via link or with a specific email.",
        "parameters": {
            "type": "object",
            "properties": {
                "spreadsheet_url_or_id": {
                    "type": "string",
                    "description": "Spreadsheet URL or ID (default: agent's configured spreadsheet)",
                },
                "share_type": {
                    "type": "string",
                    "description": "Share type: 'link' or 'email' (default: 'link')",
                },
                "link_scope": {
                    "type": "string",
                    "description": "Link scope: 'anyone' or 'domain' (default: 'anyone')",
                },
                "email": {
                    "type": "string",
                    "description": "Email address to share with (required when share_type is 'email')",
                },
                "role": {
                    "type": "string",
                    "description": "Role to assign: 'reader', 'writer', or 'owner' (default: 'reader')",
                },
            },
            "required": [],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    """Share a spreadsheet by delegating to GoogleSheetsAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("GoogleSheetsAction")
    if action is None:
        return {"error": "GoogleSheetsAction not found on this agent"}

    return await action.share_spreadsheet(
        spreadsheet_url_or_id=arguments.get("spreadsheet_url_or_id"),
        share_type=arguments.get("share_type", "link"),
        link_scope=arguments.get("link_scope", "anyone"),
        email=arguments.get("email"),
        role=arguments.get("role", "reader"),
    )
