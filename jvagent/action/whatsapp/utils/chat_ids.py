"""WhatsApp chat id helpers (group JIDs vs participant phones)."""

from __future__ import annotations

import re
from typing import Any, Dict, Mapping, Optional

_E164_MAX_DIGITS = 15
_PHONE_PATTERN = re.compile(r"[+\d][\d\s()-]*")


def strip_whatsapp_suffix(chat_id: str) -> str:
    """Remove common WhatsApp JID suffixes."""
    s = str(chat_id or "").strip()
    for suffix in ("@g.us", "@c.us", "@lid"):
        s = s.replace(suffix, "")
    return s


def is_whatsapp_group_chat_id(chat_id: str) -> bool:
    """True when *chat_id* is a group thread id, not a participant phone."""
    raw = str(chat_id or "").strip()
    if not raw:
        return False
    if "@g.us" in raw:
        return True
    cleaned = strip_whatsapp_suffix(raw)
    digits = re.sub(r"\D", "", cleaned)
    if not digits:
        return False
    if len(digits) > _E164_MAX_DIGITS:
        return True
    return False


def is_valid_whatsapp_phone(value: str) -> bool:
    """True for E.164-like phones; false for group ids and non-phones."""
    text = str(value or "").strip()
    if not text or text.lower() == "declined":
        return False
    if is_whatsapp_group_chat_id(text):
        return False
    digits = re.sub(r"\D", "", text)
    if not digits or len(digits) < 6 or len(digits) > _E164_MAX_DIGITS:
        return False
    return bool(_PHONE_PATTERN.fullmatch(text))


def is_group_whatsapp_turn(
    payload: Optional[Mapping[str, Any]],
    user_id: Optional[str] = None,
) -> bool:
    """True when the inbound turn is a group chat (flag or group JID user_id)."""
    if payload and payload.get("isGroup"):
        return True
    return is_whatsapp_group_chat_id(str(user_id or ""))


def raw_author_from_payload(payload: Optional[Mapping[str, Any]]) -> str:
    """Stripped ``author`` from payload, even when not yet a valid phone."""
    if not payload:
        return ""
    return strip_whatsapp_suffix(str(payload.get("author") or ""))


def lid_jid_for_conversion(raw_author: str) -> str:
    """Format an author id for ``convert_lid_to_phone_number``."""
    text = str(raw_author or "").strip()
    if not text:
        return ""
    if "@" in text:
        return text
    return f"{strip_whatsapp_suffix(text)}@lid"


def participant_phone_from_payload(
    payload: Optional[Mapping[str, Any]],
    user_id: Optional[str] = None,
) -> str:
    """Participant phone for a group inbound message (``author``), or ``""``."""
    if not is_group_whatsapp_turn(payload, user_id):
        return ""
    author = raw_author_from_payload(payload)
    if is_valid_whatsapp_phone(author):
        return author
    return ""


def whatsapp_payload_from_visitor_data(data: Any) -> Dict[str, Any]:
    """Read ``whatsapp_payload`` from tool visitor ``data``, or ``{}``."""
    if not isinstance(data, dict):
        return {}
    payload = data.get("whatsapp_payload") or {}
    return payload if isinstance(payload, dict) else {}
