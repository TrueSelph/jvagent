"""Canonical outbound email shape shared by all providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class EmailAttachment:
    filename: str
    content_base64: str
    content_type: str = "application/octet-stream"
    disposition: Optional[str] = None
    content_id: Optional[str] = None


@dataclass
class EmailRecipient:
    """Secondary recipient (e.g. CC)."""

    email: str
    name: Optional[str] = None


@dataclass
class CanonicalSendMessage:
    """One logical message jvagent sends; each provider maps this to its API."""

    to_email: str
    subject: str
    sender_email: str
    html_content: Optional[str] = None
    text_content: Optional[str] = None
    to_name: Optional[str] = None
    sender_name: Optional[str] = None
    reply_to: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    attachments: List[EmailAttachment] = field(default_factory=list)
    cc: List[EmailRecipient] = field(default_factory=list)


def normalize_attachments_from_body(items: Any) -> List[EmailAttachment]:
    """Build attachment list from API / metadata dicts (snake or camel case)."""
    if not items:
        return []
    if not isinstance(items, list):
        return []
    out: List[EmailAttachment] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        b64 = raw.get("content_base64") or raw.get("content")
        fname = raw.get("filename") or raw.get("name")
        if not b64 or not fname:
            continue
        out.append(
            EmailAttachment(
                filename=str(fname),
                content_base64=str(b64),
                content_type=str(
                    raw.get("type")
                    or raw.get("contentType")
                    or "application/octet-stream"
                ),
                disposition=(str(d) if (d := raw.get("disposition")) else None),
                content_id=(
                    str(c)
                    if (c := raw.get("content_id") or raw.get("contentId"))
                    else None
                ),
            )
        )
    return out
