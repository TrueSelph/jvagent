"""Create an Outlook Calendar event via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict, Optional


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "create_event",
        "description": "Create an event in Outlook Calendar.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Event title/subject",
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
                    "description": "Event description/body",
                },
                "location": {
                    "type": "string",
                    "description": "Event location",
                },
            },
            "required": ["summary", "start_time", "end_time"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Any:
    """Create an Outlook Calendar event by delegating to MicrosoftOutlookCalendarAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("MicrosoftOutlookCalendarAction")
    if action is None:
        return {"error": "MicrosoftOutlookCalendarAction not found on this agent"}

    return await action.create_event(
        summary=arguments["summary"],
        start_time=arguments["start_time"],
        end_time=arguments["end_time"],
        calendar_id=arguments.get("calendar_id", "primary"),
        description=arguments.get("description"),
        location=arguments.get("location"),
    )
