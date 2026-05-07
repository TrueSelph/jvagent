"""Mark a Gmail message as read via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "mark_read",
        "description": "Mark a Gmail message as read.",
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "The ID of the message to mark as read",
                },
                "user_id": {
                    "type": "string",
                    "description": "User identifier (default: 'me')",
                },
            },
            "required": ["message_id"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Any:
    """Mark a Gmail message as read by delegating to GoogleGmailAction."""
    from jvagent.skills._action_helpers import resolve_action

    action, err = await resolve_action(visitor, "GoogleGmailAction")
    if err:
        return err

    return await action.mark_read(
        message_id=arguments["message_id"],
        user_id=arguments.get("user_id", "me"),
    )
