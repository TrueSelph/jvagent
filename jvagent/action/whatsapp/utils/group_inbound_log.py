"""Group inbound context for WhatsApp ``group_users`` persistence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GroupInboundContext:
    group_id: str
    group_name: str
    sender_phone: str
    sender_name: str
    author_raw: str


def group_title_from_chat_api_response(result: Any) -> str:
    """Best-effort group display name from wwebjs ``getChatById`` / similar."""
    if not isinstance(result, dict):
        return ""
    roots: list[Any] = [result]
    for key in ("chat", "data", "response"):
        nested = result.get(key)
        if isinstance(nested, dict):
            roots.append(nested)
    for root in roots:
        if not isinstance(root, dict):
            continue
        for key in ("name", "formattedTitle", "subject"):
            title = root.get(key)
            if isinstance(title, str) and title.strip():
                return title.strip()
        meta = root.get("groupMetadata")
        if isinstance(meta, dict):
            subject = meta.get("subject")
            if isinstance(subject, str) and subject.strip():
                return subject.strip()
    return ""


def build_group_inbound_context(data: Any) -> GroupInboundContext:
    """Group conversation id and sender display name from the inbound payload."""
    return GroupInboundContext(
        group_id=str(getattr(data, "sender", "") or "").strip(),
        group_name="",
        sender_phone="",
        sender_name=str(getattr(data, "sender_name", "") or "").strip(),
        author_raw=str(getattr(data, "author", "") or "").strip(),
    )


__all__ = [
    "GroupInboundContext",
    "build_group_inbound_context",
    "group_title_from_chat_api_response",
]
