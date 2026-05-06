import logging
from typing import Any, ClassVar, Dict, List, Optional

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

    async def get_tools(self) -> List[Any]:
        from jvagent.tooling.tool import Tool

        action = self

        async def _list_events(limit: int = 10) -> str:
            import json

            results = await action.list_events(max_results=limit)
            return json.dumps(results, indent=2)

        async def _create_event(summary: str, start_time: str, end_time: str) -> str:
            import json

            result = await action.create_event(
                summary=summary, start_time=start_time, end_time=end_time
            )
            return json.dumps(result, indent=2)

        return [
            Tool(
                name="calendar__list_events",
                description="List upcoming calendar events.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Max events to return (default 10).",
                            "default": 10,
                        },
                    },
                },
                execute=_list_events,
            ),
            Tool(
                name="calendar__create_event",
                description="Create a new calendar event.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string", "description": "Event title."},
                        "start_time": {
                            "type": "string",
                            "description": "Start time in ISO 8601 format.",
                        },
                        "end_time": {
                            "type": "string",
                            "description": "End time in ISO 8601 format.",
                        },
                    },
                    "required": ["summary", "start_time", "end_time"],
                },
                execute=_create_event,
            ),
        ]
