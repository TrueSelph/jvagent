"""List Outlook mail messages via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict, List


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "list_messages",
        "description": "List Outlook mail messages matching a query.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (default: '')",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of messages to return (default: 10)",
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
    """List Outlook messages by delegating to MicrosoftOutlookMailAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return [{"error": "ActionResolver not available"}]

    action = await resolver.resolve("MicrosoftOutlookMailAction")
    if action is None:
        return [{"error": "MicrosoftOutlookMailAction not found on this agent"}]

    return await action.list_messages(
        query=arguments.get("query", ""),
        max_results=arguments.get("max_results", 10),
        user_id=arguments.get("user_id", "me"),
    )
