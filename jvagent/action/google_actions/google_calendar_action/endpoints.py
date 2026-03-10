"""API endpoints for Google Calendar action."""

import logging
from typing import Any, Dict, List, Optional

from jvspatial.api import endpoint
from jvspatial.api.exceptions import ResourceNotFoundError

from .google_calendar_action import GoogleCalendarAction

logger = logging.getLogger(__name__)

async def _get_calendar_action(action_id: str):
    """Resolve action by ID; validate it is a GoogleCalendarAction."""
    action = await GoogleCalendarAction.get(action_id)
    if action and isinstance(action, GoogleCalendarAction):
        return action
    return None

@endpoint(
    "/actions/{action_id}/google_calendar/auth_url",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Google Calendar Action"],
)
async def get_calendar_auth_url(action_id: str) -> Dict[str, Any]:
    """Get the Google OAuth2 authorization URL."""
    action = await _get_calendar_action(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Google Calendar action {action_id} not found")

    auth_url = await action.get_authorization_url()
    return {"success": True, "auth_url": auth_url}

@endpoint(
    "/actions/{action_id}/google_calendar/authorize",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Google Calendar Action"],
)
async def authorize_calendar(action_id: str, code: str) -> Dict[str, Any]:
    """Exchange the authorization code for credentials."""
    action = await _get_calendar_action(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Google Calendar action {action_id} not found")

    success = await action.authorize(code)
    return {"success": success}

@endpoint(
    "/actions/{action_id}/google_calendar/list",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Google Calendar Action"],
)
async def list_calendar_events(
    action_id: str, 
    calendar_id: str = 'primary', 
    time_min: Optional[str] = None, 
    max_results: int = 10
) -> Dict[str, Any]:
    """List calendar events."""
    action = await _get_calendar_action(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Google Calendar action {action_id} not found")

    events = await action.list_events(calendar_id=calendar_id, time_min=time_min, max_results=max_results)
    return {"success": True, "events": events}

@endpoint(
    "/actions/{action_id}/google_calendar/create",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Google Calendar Action"],
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
    """Create a new calendar event."""
    action = await _get_calendar_action(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Google Calendar action {action_id} not found")

    result = await action.create_event(
        summary=summary,
        start_time=start_time,
        end_time=end_time,
        calendar_id=calendar_id,
        description=description,
        location=location,
    )
    return {"success": True, "event": result}

@endpoint(
    "/actions/{action_id}/google_calendar/delete",
    methods=["DELETE"],
    auth=True,
    roles=["admin"],
    tags=["Google Calendar Action"],
)
async def delete_calendar_event(action_id: str, calendar_id: str, event_id: str) -> Dict[str, Any]:
    """Delete a calendar event."""
    action = await _get_calendar_action(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Google Calendar action {action_id} not found")

    await action.delete_event(calendar_id=calendar_id, event_id=event_id)
    return {"success": True}
