"""List Outlook inbox messages via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict, List


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "list_inbox_messages",
        "description": "List Outlook inbox messages with OData filtering.",
        "parameters": {
            "type": "object",
            "properties": {
                "odata_filter": {
                    "type": "string",
                    "description": "OData filter expression (default: 'isRead eq false')",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of messages to return (default: 25)",
                },
                "user_id": {
                    "type": "string",
                    "description": "User identifier (default: 'me')",
                },
            },
            "required": [],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> List[Dict[str, Any]]:
    """List Outlook inbox messages by delegating to MicrosoftOutlookMailAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return [{"error": "ActionResolver not available"}]

    action = await resolver.resolve("MicrosoftOutlookMailAction")
    if action is None:
        return [{"error": "MicrosoftOutlookMailAction not found on this agent"}]

    return await action.list_inbox_messages(
        odata_filter=arguments.get("odata_filter", "isRead eq false"),
        max_results=arguments.get("max_results", 25),
        user_id=arguments.get("user_id", "me"),
    )
