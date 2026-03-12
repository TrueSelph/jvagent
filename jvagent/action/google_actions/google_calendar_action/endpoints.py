"""API endpoints for Google Calendar action."""

import logging
from typing import Any, Dict, List, Optional

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError

from .google_calendar_action import GoogleCalendarAction

logger = logging.getLogger(__name__)


async def _get_calendar_action(action_id: str) -> Optional[GoogleCalendarAction]:
    """Resolve action by ID; validate it is a GoogleCalendarAction.

    **Args:**

    - action_id: ID of the action to retrieve

    **Returns:**

    GoogleCalendarAction instance if found and valid, else None
    """
    action = await GoogleCalendarAction.get(action_id)
    if action and isinstance(action, GoogleCalendarAction):
        return action
    return None


@endpoint(
    "/actions/{action_id}/google_calendar/list",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Google Calendar Action"],
    summary="List events on a Google Calendar",
    response=success_response(
        data={
            "events": ResponseField(
                field_type=List[Dict[str, Any]],
                description="List of calendar events",
                example=[
                    {
                        "id": "abc123xyz",
                        "summary": "Team Standup",
                        "start": {"dateTime": "2024-06-01T09:00:00-04:00"},
                        "end": {"dateTime": "2024-06-01T09:30:00-04:00"},
                        "location": "Conference Room A",
                        "description": "Daily team standup meeting",
                    }
                ],
            ),
            "success": ResponseField(
                field_type=bool,
                description="Whether the retrieval was successful",
                example=True,
            ),
        }
    ),
)
async def list_calendar_events(
    action_id: str,
    calendar_id: str = "primary",
    time_min: Optional[str] = None,
    max_results: int = 10,
) -> Dict[str, Any]:
    """List upcoming events on a Google Calendar.

    **Overview:**

    Retrieves a list of upcoming events from a specified calendar, ordered by start time.

    **Args:**

    - action_id: ID of the Google Calendar action
    - calendar_id: Calendar to retrieve events from. Use \"primary\" for the main calendar. default=\"primary\"
    - time_min: Optional lower bound (RFC3339 timestamp) for event start time, e.g. \"2024-06-01T00:00:00Z\"
    - max_results: Maximum number of events to return. default=10

    **Returns:**

    Dictionary containing:
    - **events**: List of event objects with id, summary, start, end, location, and description
    - **success**: Always True if retrieval completes

    **Raises:**

    - ResourceNotFoundError: If the Google Calendar action is not found
    """
    action = await _get_calendar_action(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Google Calendar action {action_id} not found",
            details={"action_id": action_id},
        )

    events = await action.list_events(
        calendar_id=calendar_id, time_min=time_min, max_results=max_results
    )
    events = await action.list_events(
        calendar_id=calendar_id, time_min=time_min, max_results=max_results
    )
    return {"success": True, "events": events}


@endpoint(
    "/actions/{action_id}/google_calendar/create",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Google Calendar Action"],
    summary="Create a Google Calendar event",
    response=success_response(
        data={
            "event": ResponseField(
                field_type=Dict[str, Any],
                description="The newly created calendar event",
                example={
                    "id": "abc123xyz",
                    "summary": "Team Standup",
                    "start": {"dateTime": "2024-06-01T09:00:00-04:00"},
                    "end": {"dateTime": "2024-06-01T09:30:00-04:00"},
                    "htmlLink": "https://www.google.com/calendar/event?eid=...",
                },
            ),
            "success": ResponseField(
                field_type=bool,
                description="Whether the event was created successfully",
                example=True,
            ),
        }
    ),
)
async def create_calendar_event(
    action_id: str,
    summary: str,
    start_time: str,
    end_time: str,
    calendar_id: str = "primary",
    description: Optional[str] = None,
    location: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new event on a Google Calendar.

    **Overview:**

    Creates a calendar event with the provided details. Start and end times must
    be provided in RFC3339 format (e.g., \"2024-06-01T09:00:00-04:00\").

    **Args:**

    - action_id: ID of the Google Calendar action
    - summary: Title of the calendar event
    - start_time: Start datetime in RFC3339 format (e.g., \"2024-06-01T09:00:00-04:00\")
    - end_time: End datetime in RFC3339 format (e.g., \"2024-06-01T09:30:00-04:00\")
    - calendar_id: Calendar to add the event to. default=\"primary\"
    - description: Optional description or notes for the event
    - location: Optional location string for the event

    **Returns:**

    Dictionary containing:
    - **event**: The created event object including its assigned id and htmlLink
    - **success**: Always True if creation completes

    **Raises:**

    - ResourceNotFoundError: If the Google Calendar action is not found
    - ValidationError: If the event creation fails
    """
    action = await _get_calendar_action(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Google Calendar action {action_id} not found",
            details={"action_id": action_id},
        )

    try:
        result = await action.create_event(
            summary=summary,
            start_time=start_time,
            end_time=end_time,
            calendar_id=calendar_id,
            description=description,
            location=location,
        )
        return {"success": True, "event": result}
    except Exception as e:
        logger.error(f"Failed to create calendar event: {e}", exc_info=True)
        raise ValidationError(
            message=f"Failed to create calendar event: {str(e)}",
            details={"action_id": action_id, "summary": summary},
        )


@endpoint(
    "/actions/{action_id}/google_calendar/delete",
    methods=["DELETE"],
    auth=True,
    roles=["admin"],
    tags=["Google Calendar Action"],
    summary="Delete a Google Calendar event",
    response=success_response(
        data={
            "success": ResponseField(
                field_type=bool,
                description="Whether the deletion was successful",
                example=True,
            ),
        }
    ),
)
async def delete_calendar_event(
    action_id: str, calendar_id: str, event_id: str
) -> Dict[str, Any]:
    """Delete an event from a Google Calendar.

    **Overview:**

    Permanently removes a calendar event by its ID from the specified calendar.

    **Args:**

    - action_id: ID of the Google Calendar action
    - calendar_id: Calendar containing the event (e.g., \"primary\")
    - event_id: Unique ID of the event to delete

    **Returns:**

    Dictionary containing:
    - **success**: True if the event was deleted successfully

    **Raises:**

    - ResourceNotFoundError: If the Google Calendar action is not found
    - ValidationError: If the deletion operation fails
    """
    action = await _get_calendar_action(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Google Calendar action {action_id} not found",
            details={"action_id": action_id},
        )

    try:
        await action.delete_event(calendar_id=calendar_id, event_id=event_id)
        return {"success": True}
    except Exception as e:
        logger.error(f"Failed to delete calendar event: {e}", exc_info=True)
        raise ValidationError(
            message=f"Failed to delete calendar event: {str(e)}",
            details={"action_id": action_id, "event_id": event_id},
        )
