"""Create a Google Calendar event via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "create_event",
        "description": "Create a new event on Google Calendar.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Event title/summary",
                },
                "start_time": {
                    "type": "string",
                    "description": "Event start time (ISO 8601)",
                },
                "end_time": {
                    "type": "string",
                    "description": "Event end time (ISO 8601)",
                },
                "calendar_id": {
                    "type": "string",
                    "description": "Calendar identifier (default: 'primary')",
                },
                "description": {
                    "type": "string",
                    "description": "Optional event description",
                },
                "location": {
                    "type": "string",
                    "description": "Optional event location",
                },
            },
            "required": ["summary", "start_time", "end_time"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    """Create a calendar event by delegating to GoogleCalendarAction."""
    from jvagent.skills._action_helpers import resolve_action

    action, err = await resolve_action(visitor, "GoogleCalendarAction")
    if err:
        return err

    return await action.create_event(
        summary=arguments["summary"],
        start_time=arguments["start_time"],
        end_time=arguments["end_time"],
        calendar_id=arguments.get("calendar_id", "primary"),
        description=arguments.get("description"),
        location=arguments.get("location"),
    )
