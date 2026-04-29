"""Get a Gmail message via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "get_message",
        "description": "Get a specific Gmail message by ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "The ID of the message to retrieve",
                },
                "user_id": {
                    "type": "string",
                    "description": "User identifier (default: 'me')",
                },
                "fmt": {
                    "type": "string",
                    "description": "Format of the message (default: 'full')",
                },
            },
            "required": ["message_id"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Any:
    """Get a Gmail message by delegating to GoogleGmailAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("GoogleGmailAction")
    if action is None:
        return {"error": "GoogleGmailAction not found on this agent"}

    return await action.get_message(
        message_id=arguments["message_id"],
        user_id=arguments.get("user_id", "me"),
        fmt=arguments.get("fmt", "full"),
    )
