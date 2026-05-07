"""Send an Outlook mail message via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "send_email",
        "description": "Send an email via Outlook.",
        "parameters": {
            "type": "object",
            "properties": {
                "data": {
                    "type": "object",
                    "description": "Email payload with 'to', 'subject', and 'body' fields",
                    "properties": {
                        "to": {
                            "type": "string",
                            "description": "Recipient email address",
                        },
                        "subject": {
                            "type": "string",
                            "description": "Email subject line",
                        },
                        "body": {"type": "string", "description": "Email body text"},
                    },
                    "required": ["to", "subject", "body"],
                },
            },
            "required": ["data"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Any:
    """Send an email by delegating to MicrosoftOutlookMailAction."""
    from jvagent.skills._action_helpers import resolve_action

    action, err = await resolve_action(visitor, "MicrosoftOutlookMailAction")
    if err:
        return err

    return await action.send_email(data=arguments["data"])
