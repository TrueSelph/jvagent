"""Microsoft Graph message JSON → inbound interaction tuple (parallel to Gmail raw RFC822)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from jvagent.action.email_action.inbound.text_utils import strip_html_to_text

InboundTuple = Tuple[str, str, Dict[str, Any]]


def _normalize_msg_id(value: str) -> str:
    return value.strip().strip("<>")


def _header_map(internet_message_headers: Any) -> Dict[str, str]:
    if not isinstance(internet_message_headers, list):
        return {}
    out: Dict[str, str] = {}
    for item in internet_message_headers:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        val = item.get("value")
        if name and val is not None:
            out[str(name)] = str(val)
    return out


def graph_message_resource_to_tuple(
    message_resource: Dict[str, Any],
) -> Optional[InboundTuple]:
    """Map Graph ``GET /me/messages/{id}`` JSON to the same tuple shape as Gmail inbound."""
    from_blob = message_resource.get("from") or {}
    if not isinstance(from_blob, dict):
        return None
    ea = from_blob.get("emailAddress") or {}
    if not isinstance(ea, dict):
        return None
    addr = (ea.get("address") or "").strip().lower()
    if not addr or "@" not in addr:
        return None
    user_id = addr
    from_name = (ea.get("name") or "").strip() or None

    subject = (message_resource.get("subject") or "").strip()
    body_obj = message_resource.get("body") or {}
    content = ""
    content_type = ""
    if isinstance(body_obj, dict):
        content = body_obj.get("content") or ""
        if not isinstance(content, str):
            content = str(content) if content is not None else ""
        content_type = (body_obj.get("contentType") or "").strip().lower()

    raw_html = ""
    raw_plain = ""
    if content_type == "html":
        raw_html = content.strip()
        raw_plain = strip_html_to_text(content).strip()
    else:
        raw_plain = content.strip()

    hmap = _header_map(message_resource.get("internetMessageHeaders"))
    mid_hdr = (
        (message_resource.get("internetMessageId") or "").strip()
        or hmap.get("Message-ID")
        or hmap.get("Message-Id")
        or ""
    )
    irt = (hmap.get("In-Reply-To") or "").strip()

    to_lines: List[str] = []
    for rec in message_resource.get("toRecipients") or []:
        if not isinstance(rec, dict):
            continue
        nea = rec.get("emailAddress") or {}
        if isinstance(nea, dict):
            a = (nea.get("address") or "").strip()
            if a:
                to_lines.append(a)
    to_hdr = ", ".join(to_lines) if to_lines else ""

    has_any = bool(subject) or bool(raw_plain) or bool(raw_html)
    if not has_any:
        return None

    utterance = subject if subject else "(no subject)"

    inbound: Dict[str, Any] = {
        "MessageId": (
            _normalize_msg_id(mid_hdr) if mid_hdr else message_resource.get("id")
        ),
        "Subject": subject,
        "InReplyTo": _normalize_msg_id(irt) if irt else None,
        "To": to_hdr or None,
        "FromName": from_name,
        "OutlookMessageId": message_resource.get("id"),
        "OutlookConversationId": message_resource.get("conversationId"),
    }
    if raw_plain:
        inbound["BodyPlain"] = raw_plain
    if raw_html:
        inbound["BodyHtml"] = raw_html

    data_dict: Dict[str, Any] = {
        "email_provider": "outlook",
        "email_inbound": inbound,
    }

    return user_id, utterance, data_dict
