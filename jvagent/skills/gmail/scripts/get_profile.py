"""Get Gmail profile via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "get_profile",
        "description": "Get the authenticated user's Gmail profile.",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "User identifier (default: 'me')",
                },
            },
            "required": [],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Any:
    """Get Gmail profile by delegating to GoogleGmailAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("GoogleGmailAction")
    if action is None:
        return {"error": "GoogleGmailAction not found on this agent"}

    return await action.get_profile(
        user_id=arguments.get("user_id", "me"),
    )
