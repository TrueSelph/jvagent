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
        """Full Google Calendar tool surface (ADR-0012: actions are first-class tools)."""
        import json

        from jvagent.tooling.tool import Tool

        action = self

        async def _list_events(
            calendar_id: str = "primary",
            time_min: Optional[str] = None,
            max_results: int = 10,
        ) -> str:
            results = await action.list_events(
                calendar_id=calendar_id,
                time_min=time_min,
                max_results=max_results,
            )
            return json.dumps(results, indent=2)

        async def _create_event(
            summary: str,
            start_time: str,
            end_time: str,
            calendar_id: str = "primary",
            description: Optional[str] = None,
            location: Optional[str] = None,
        ) -> str:
            result = await action.create_event(
                summary=summary,
                start_time=start_time,
                end_time=end_time,
                calendar_id=calendar_id,
                description=description,
                location=location,
            )
            return json.dumps(result, indent=2)

        async def _delete_event(calendar_id: str, event_id: str) -> str:
            result = await action.delete_event(
                calendar_id=calendar_id,
                event_id=event_id,
            )
            return json.dumps({"deleted": result}, indent=2)

        return [
            Tool(
                name="calendar__list_events",
                description="List upcoming events from Google Calendar.",
                parameters_schema={
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
                execute=_list_events,
            ),
            Tool(
                name="calendar__create_event",
                description="Create a new event on Google Calendar.",
                parameters_schema={
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
                execute=_create_event,
            ),
            Tool(
                name="calendar__delete_event",
                description="Delete an event from Google Calendar.",
                parameters_schema={
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
                execute=_delete_event,
            ),
        ]
