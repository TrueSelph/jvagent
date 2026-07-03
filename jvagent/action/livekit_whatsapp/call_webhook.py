"""Parse Meta WhatsApp Cloud API call webhooks (field=calls)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class WhatsAppCallEvent:
    """Normalized inbound call webhook event from Meta."""

    call_id: str
    event: str  # connect | terminate | ring | etc.
    sdp: str
    sdp_type: str
    phone_number_id: str
    from_number: str
    to_number: str
    contact_name: str


def is_calls_webhook(request: Dict[str, Any]) -> bool:
    """Return True when the Meta envelope contains a calls field change."""
    if request.get("object") != "whatsapp_business_account":
        return False
    for entry in request.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue
            if change.get("field") == "calls":
                return True
    return False


def parse_calls_webhook(request: Dict[str, Any]) -> List[WhatsAppCallEvent]:
    """Extract call events from a Meta calls webhook payload."""
    events: List[WhatsAppCallEvent] = []
    for entry in request.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            if not isinstance(change, dict) or change.get("field") != "calls":
                continue
            value = change.get("value") or {}
            if not isinstance(value, dict):
                continue
            metadata = value.get("metadata") or {}
            phone_number_id = str(metadata.get("phone_number_id") or "")
            contacts = _contact_name_map(value)
            for call in value.get("calls") or []:
                if not isinstance(call, dict):
                    continue
                parsed = _parse_call_object(call, phone_number_id, contacts)
                if parsed is not None:
                    events.append(parsed)
    return events


def _contact_name_map(value: Dict[str, Any]) -> Dict[str, str]:
    names: Dict[str, str] = {}
    for contact in value.get("contacts") or []:
        if not isinstance(contact, dict):
            continue
        wa_id = str(contact.get("wa_id") or "")
        profile = contact.get("profile") or {}
        name = ""
        if isinstance(profile, dict):
            name = str(profile.get("name") or "").strip()
        if wa_id:
            names[wa_id] = name
    return names


def _parse_call_object(
    call: Dict[str, Any],
    phone_number_id: str,
    contacts: Dict[str, str],
) -> Optional[WhatsAppCallEvent]:
    call_id = str(call.get("id") or "").strip()
    if not call_id:
        return None
    event = str(call.get("event") or "").strip().lower()
    from_number = str(call.get("from") or "").strip()
    to_number = str(call.get("to") or "").strip()
    session = call.get("session") or {}
    sdp = ""
    sdp_type = ""
    if isinstance(session, dict):
        sdp = str(session.get("sdp") or "")
        sdp_type = str(session.get("sdp_type") or "").strip().lower()
    contact_name = contacts.get(from_number, "")
    return WhatsAppCallEvent(
        call_id=call_id,
        event=event,
        sdp=sdp,
        sdp_type=sdp_type,
        phone_number_id=phone_number_id,
        from_number=from_number,
        to_number=to_number,
        contact_name=contact_name,
    )
