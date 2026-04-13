import logging
from typing import Any, ClassVar, Dict, List, Optional
from urllib.parse import quote

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
