import json
import logging
from typing import Annotated, Any, ClassVar, Dict, List, Optional

from jvagent.tooling.tool_decorator import tool

from ..google_action import GoogleAction

logger = logging.getLogger(__name__)


class GoogleCalendarAction(GoogleAction):
    """Action for Google Calendar operations using OAuth2 (user-delegated credentials)."""

    API_SERVICE_NAME: ClassVar[str] = "calendar"
    API_VERSION: ClassVar[str] = "v3"
    SCOPES: ClassVar[List[str]] = ["https://www.googleapis.com/auth/calendar"]

    async def list_events(
        self,
        calendar_id: str = "primary",
        time_min: Optional[str] = None,
        max_results: int = 10,
    ) -> List[Dict[str, Any]]:
        """List events on a calendar."""
        service = await self.get_service()
        events_result = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=time_min,
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        return events_result.get("items", [])

    async def create_event(
        self,
        summary: str,
        start_time: str,
        end_time: str,
        calendar_id: str = "primary",
        description: Optional[str] = None,
        location: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new event on a calendar."""
        service = await self.get_service()
        event = {
            "summary": summary,
            "location": location,
            "description": description,
            "start": {"dateTime": start_time},
            "end": {"dateTime": end_time},
        }
        return service.events().insert(calendarId=calendar_id, body=event).execute()

    async def delete_event(self, calendar_id: str, event_id: str) -> bool:
        """Delete an event from a calendar."""
        service = await self.get_service()
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        return True

    @tool(name="calendar__list_events")
    async def _t_list_events(
        self,
        calendar_id: Annotated[
            Optional[str], "Calendar identifier (default: 'primary')"
        ] = None,
        time_min: Annotated[
            Optional[str], "Lower bound for event start time (ISO 8601)"
        ] = None,
        max_results: Annotated[
            Optional[int], "Maximum number of events to return (default: 10)"
        ] = None,
    ) -> str:
        """List upcoming events from Google Calendar."""
        results = await self.list_events(
            calendar_id=calendar_id or "primary",
            time_min=time_min,
            max_results=max_results if max_results is not None else 10,
        )
        return json.dumps(results, indent=2)

    @tool(name="calendar__create_event")
    async def _t_create_event(
        self,
        summary: Annotated[str, "Event title/summary"],
        start_time: Annotated[str, "Event start time (ISO 8601)"],
        end_time: Annotated[str, "Event end time (ISO 8601)"],
        calendar_id: Annotated[
            Optional[str], "Calendar identifier (default: 'primary')"
        ] = None,
        description: Annotated[Optional[str], "Optional event description"] = None,
        location: Annotated[Optional[str], "Optional event location"] = None,
    ) -> str:
        """Create a new event on Google Calendar."""
        result = await self.create_event(
            summary=summary,
            start_time=start_time,
            end_time=end_time,
            calendar_id=calendar_id or "primary",
            description=description,
            location=location,
        )
        return json.dumps(result, indent=2)

    @tool(name="calendar__delete_event")
    async def _t_delete_event(
        self,
        calendar_id: Annotated[str, "Calendar identifier (default: 'primary')"],
        event_id: Annotated[str, "The ID of the event to delete"],
    ) -> str:
        """Delete an event from Google Calendar."""
        result = await self.delete_event(
            calendar_id=calendar_id,
            event_id=event_id,
        )
        return json.dumps({"deleted": result}, indent=2)
