"""List Google Calendar events via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "list_events",
        "description": "List upcoming events from Google Calendar.",
        "parameters": {
            "type": "object",
            "properties": {
                "calendar_id": {
                    "type": "string",
                    "description": "Calendar identifier (default: 'primary')",
                },
                "time_min": {
                    "type": "string",
                    "description": "Lower bound for event start time (ISO 8601)",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of events to return (default: 10)",
                },
            },
            "required": [],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> List[Dict[str, Any]]:
    """List calendar events by delegating to GoogleCalendarAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return [{"error": "ActionResolver not available"}]

    action = await resolver.resolve("GoogleCalendarAction")
    if action is None:
        return [{"error": "GoogleCalendarAction not found on this agent"}]

    return await action.list_events(
        calendar_id=arguments.get("calendar_id", "primary"),
        time_min=arguments.get("time_min"),
        max_results=arguments.get("max_results", 10),
    )
