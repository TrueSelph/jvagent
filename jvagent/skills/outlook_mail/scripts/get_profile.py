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
    from jvagent.skills._action_helpers import resolve_action

    action, err = await resolve_action(visitor, "MicrosoftOutlookMailAction")
    if err:
        return err

    return await action.get_profile(
        user_id=arguments.get("user_id", "me"),
    )
