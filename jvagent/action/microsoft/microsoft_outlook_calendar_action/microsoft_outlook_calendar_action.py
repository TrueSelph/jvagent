import logging
from typing import Annotated, Any, ClassVar, Dict, List, Optional
from urllib.parse import quote

from jvagent.tooling.tool_decorator import tool

from ..microsoft_action import MicrosoftAction

logger = logging.getLogger(__name__)


class MicrosoftOutlookCalendarAction(MicrosoftAction):
    """Outlook calendar via Microsoft Graph."""

    SCOPES: ClassVar[List[str]] = [
        "offline_access",
        "User.Read",
        "Calendars.ReadWrite",
    ]

    def _calendar_path(self, calendar_id: str) -> str:
        if not calendar_id or calendar_id == "primary":
            return "/me/events"
        safe = quote(calendar_id, safe="")
        return f"/me/calendars/{safe}/events"

    async def list_events(
        self,
        calendar_id: str = "primary",
        time_min: Optional[str] = None,
        max_results: int = 10,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {
            "$top": max(1, min(max_results, 999)),
            "$orderby": "start/dateTime",
        }
        if time_min:
            filt = f"start/dateTime ge {time_min}"
            params["$filter"] = filt
        path = self._calendar_path(calendar_id)
        data = await self.graph_json("GET", path, params=params)
        items = data.get("value") if isinstance(data, dict) else None
        if items is None:
            return []
        norm: List[Dict[str, Any]] = []
        for ev in items:
            norm.append(
                {
                    "id": ev.get("id"),
                    "summary": ev.get("subject"),
                    "start": ev.get("start"),
                    "end": ev.get("end"),
                    "location": (ev.get("location") or {}).get("displayName"),
                    "description": ev.get("bodyPreview")
                    or ev.get("body", {}).get("content"),
                    "webLink": ev.get("webLink"),
                }
            )
        return norm

    async def create_event(
        self,
        summary: str,
        start_time: str,
        end_time: str,
        calendar_id: str = "primary",
        description: Optional[str] = None,
        location: Optional[str] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "subject": summary,
            "start": {"dateTime": start_time, "timeZone": "UTC"},
            "end": {"dateTime": end_time, "timeZone": "UTC"},
        }
        if description:
            body["body"] = {"contentType": "Text", "content": description}
        if location:
            body["location"] = {"displayName": location}
        path = self._calendar_path(calendar_id)
        created = await self.graph_json("POST", path, json_body=body, ok=(201, 200))
        return {
            "id": created.get("id"),
            "summary": created.get("subject"),
            "start": created.get("start"),
            "end": created.get("end"),
            "htmlLink": created.get("webLink"),
        }

    async def delete_event(self, calendar_id: str, event_id: str) -> bool:
        if calendar_id in ("", "primary"):
            path = f"/me/events/{quote(event_id, safe='')}"
        else:
            cal = quote(calendar_id, safe="")
            eid = quote(event_id, safe="")
            path = f"/me/calendars/{cal}/events/{eid}"
        await self.graph_json("DELETE", path, ok=(204,))
        return True

    @tool(name="outlook_calendar__list_events")
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
        """List upcoming events from Outlook Calendar."""
        import json

        calendar_id = calendar_id if calendar_id is not None else "primary"
        max_results = max_results if max_results is not None else 10
        results = await self.list_events(
            calendar_id=calendar_id,
            time_min=time_min,
            max_results=max_results,
        )
        return json.dumps(results, indent=2)

    @tool(name="outlook_calendar__create_event")
    async def _t_create_event(
        self,
        summary: Annotated[str, "Event title/subject"],
        start_time: Annotated[str, "Event start time (ISO 8601)"],
        end_time: Annotated[str, "Event end time (ISO 8601)"],
        calendar_id: Annotated[
            Optional[str], "Calendar identifier (default: 'primary')"
        ] = None,
        description: Annotated[Optional[str], "Event description/body"] = None,
        location: Annotated[Optional[str], "Event location"] = None,
    ) -> str:
        """Create an event in Outlook Calendar."""
        import json

        calendar_id = calendar_id if calendar_id is not None else "primary"
        result = await self.create_event(
            summary=summary,
            start_time=start_time,
            end_time=end_time,
            calendar_id=calendar_id,
            description=description,
            location=location,
        )
        return json.dumps(result, indent=2)

    @tool(name="outlook_calendar__delete_event")
    async def _t_delete_event(
        self,
        calendar_id: Annotated[str, "Calendar identifier (default: 'primary')"],
        event_id: Annotated[str, "The ID of the event to delete"],
    ) -> str:
        """Delete an event from Outlook Calendar."""
        import json

        result = await self.delete_event(
            calendar_id=calendar_id,
            event_id=event_id,
        )
        return json.dumps({"deleted": result}, indent=2)
