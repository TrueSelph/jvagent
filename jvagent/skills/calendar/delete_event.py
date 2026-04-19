"""Delete a Google Calendar event via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "delete_event",
        "description": "Delete an event from Google Calendar.",
        "parameters": {
            "type": "object",
            "properties": {
                "calendar_id": {
                    "type": "string",
                    "description": "Calendar identifier (default: 'primary')",
                },
                "event_id": {
                    "type": "string",
                    "description": "The ID of the event to delete",
                },
            },
            "required": ["calendar_id", "event_id"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Any:
    """Delete a calendar event by delegating to GoogleCalendarAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("GoogleCalendarAction")
    if action is None:
        return {"error": "GoogleCalendarAction not found on this agent"}

    result = await action.delete_event(
        calendar_id=arguments["calendar_id"],
        event_id=arguments["event_id"],
    )
    return {"deleted": result}
