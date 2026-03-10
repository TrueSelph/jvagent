import logging
from typing import Any, ClassVar, Dict, List, Optional

from ..google_action import GoogleAction

logger = logging.getLogger(__name__)

class GoogleCalendarAction(GoogleAction):
    """Action for Google Calendar operations using a service account."""

    API_SERVICE_NAME: ClassVar[str] = 'calendar'
    API_VERSION: ClassVar[str] = 'v3'
    SCOPES: ClassVar[List[str]] = ['https://www.googleapis.com/auth/calendar']

    async def list_events(
        self, 
        calendar_id: str = 'primary', 
        time_min: Optional[str] = None, 
        max_results: int = 10
    ) -> List[Dict[str, Any]]:
        """List events on a calendar."""
        service = await self.get_service()
        events_result = service.events().list(
            calendarId=calendar_id, 
            timeMin=time_min,
            maxResults=max_results, 
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        return events_result.get('items', [])

    async def create_event(
        self, 
        summary: str, 
        start_time: str, 
        end_time: str, 
        calendar_id: str = 'primary',
        description: Optional[str] = None,
        location: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a new event on a calendar."""
        service = await self.get_service()
        event = {
            'summary': summary,
            'location': location,
            'description': description,
            'start': {'dateTime': start_time},
            'end': {'dateTime': end_time},
        }
        return service.events().insert(calendarId=calendar_id, body=event).execute()

    async def delete_event(self, calendar_id: str, event_id: str) -> bool:
        """Delete an event from a calendar."""
        service = await self.get_service()
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        return True
