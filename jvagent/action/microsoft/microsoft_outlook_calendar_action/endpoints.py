"""API endpoints for Microsoft Outlook calendar action."""

import logging
from typing import Any, Dict, List, Optional

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ValidationError

from jvagent.action.utils.endpoint_helpers import require_typed_action

from .microsoft_outlook_calendar_action import MicrosoftOutlookCalendarAction

logger = logging.getLogger(__name__)


@endpoint(
    "/actions/{action_id}/list",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Microsoft Outlook Calendar Action"],
    summary="List events on an Outlook calendar (Microsoft Graph)",
    response=success_response(
        data={
            "events": ResponseField(
                field_type=List[Dict[str, Any]],
                description="List of calendar events",
                example=[
                    {
                        "id": "AQMkADAw...",
                        "summary": "Team standup",
                        "start": {"dateTime": "2024-06-01T09:00:00-04:00"},
                        "end": {"dateTime": "2024-06-01T09:30:00-04:00"},
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
async def list_outlook_calendar_events(
    action_id: str,
    calendar_id: str = "primary",
    time_min: Optional[str] = None,
    max_results: int = 10,
) -> Dict[str, Any]:
    """List upcoming events from Outlook calendar via Microsoft Graph."""
    action = await require_typed_action(
        action_id,
        MicrosoftOutlookCalendarAction,
        not_found_message=(
            f"Microsoft Outlook calendar action {action_id} not found"
        ),
        wrong_type_message=(
            f"Action '{action_id}' is not a MicrosoftOutlookCalendarAction"
        ),
    )

    events = await action.list_events(
        calendar_id=calendar_id, time_min=time_min, max_results=max_results
    )
    return {"success": True, "events": events}


@endpoint(
    "/actions/{action_id}/create",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Microsoft Outlook Calendar Action"],
    summary="Create an Outlook calendar event",
    response=success_response(
        data={
            "event": ResponseField(
                field_type=Dict[str, Any],
                description="The newly created calendar event",
                example={
                    "id": "AQMkADAw...",
                    "summary": "Team standup",
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
async def create_outlook_calendar_event(
    action_id: str,
    summary: str,
    start_time: str,
    end_time: str,
    calendar_id: str = "primary",
    description: Optional[str] = None,
    location: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new event on an Outlook calendar."""
    action = await require_typed_action(
        action_id,
        MicrosoftOutlookCalendarAction,
        not_found_message=(
            f"Microsoft Outlook calendar action {action_id} not found"
        ),
        wrong_type_message=(
            f"Action '{action_id}' is not a MicrosoftOutlookCalendarAction"
        ),
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
        logger.error("Failed to create Outlook calendar event: %s", e, exc_info=True)
        raise ValidationError(
            message=f"Failed to create calendar event: {e}",
            details={"action_id": action_id, "summary": summary},
        )


@endpoint(
    "/actions/{action_id}/delete",
    methods=["DELETE"],
    auth=True,
    roles=["admin"],
    tags=["Microsoft Outlook Calendar Action"],
    summary="Delete an Outlook calendar event",
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
async def delete_outlook_calendar_event(
    action_id: str, calendar_id: str, event_id: str
) -> Dict[str, Any]:
    """Delete an event from an Outlook calendar."""
    action = await require_typed_action(
        action_id,
        MicrosoftOutlookCalendarAction,
        not_found_message=(
            f"Microsoft Outlook calendar action {action_id} not found"
        ),
        wrong_type_message=(
            f"Action '{action_id}' is not a MicrosoftOutlookCalendarAction"
        ),
    )

    try:
        await action.delete_event(calendar_id=calendar_id, event_id=event_id)
        return {"success": True}
    except Exception as e:
        logger.error("Failed to delete Outlook calendar event: %s", e, exc_info=True)
        raise ValidationError(
            message=f"Failed to delete calendar event: {e}",
            details={"action_id": action_id, "event_id": event_id},
        )
