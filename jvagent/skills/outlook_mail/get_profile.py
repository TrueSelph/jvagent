"""Get Outlook mail profile via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "get_profile",
        "description": "Get the authenticated user's Outlook mail profile.",
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
    """Get Outlook mail profile by delegating to MicrosoftOutlookMailAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("MicrosoftOutlookMailAction")
    if action is None:
        return {"error": "MicrosoftOutlookMailAction not found on this agent"}

    return await action.get_profile(
        user_id=arguments.get("user_id", "me"),
    )
