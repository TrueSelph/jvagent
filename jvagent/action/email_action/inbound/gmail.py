"""Gmail API inbound: parse ``users.messages.get`` with ``format=raw``."""

from __future__ import annotations

import base64
import logging
from email import message_from_bytes
from email.message import Message
from email.policy import default as email_policy
from email.utils import parseaddr
from typing import Any, Dict, List, Optional, Tuple

from jvagent.action.email_action.inbound.text_utils import strip_html_to_text

logger = logging.getLogger(__name__)

InboundTuple = Tuple[str, str, Dict[str, Any]]


def _pad_b64url(data: str) -> str:
    pad = len(data) % 4
    if pad:
        return data + "=" * (4 - pad)
    return data


def decode_gmail_raw_b64(raw_b64url: str) -> bytes:
    """Decode Gmail API ``raw`` (URL-safe base64, possibly unpadded)."""
    return base64.urlsafe_b64decode(_pad_b64url(raw_b64url.strip()))


def _collect_text_parts(msg: Message) -> Tuple[Optional[str], Optional[str]]:
    plain: Optional[str] = None
    html: Optional[str] = None
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain" and plain is None:
                blob = part.get_payload(decode=True)
                if isinstance(blob, bytes):
                    plain = blob.decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
            elif ctype == "text/html" and html is None:
                blob = part.get_payload(decode=True)
                if isinstance(blob, bytes):
                    html = blob.decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
    else:
        ctype = msg.get_content_type()
        blob = msg.get_payload(decode=True)
        if isinstance(blob, bytes):
            charset = msg.get_content_charset() or "utf-8"
            text = blob.decode(charset, errors="replace")
            if ctype == "text/html":
                html = text
            else:
                plain = text
    return plain, html


def _normalize_msg_id(value: str) -> str:
    return value.strip().strip("<>")


def gmail_raw_message_to_tuple(
    message_resource: Dict[str, Any],
) -> Optional[InboundTuple]:
    """Map Gmail ``messages.get`` JSON (with ``raw``) to interaction tuple."""
    raw_b64 = message_resource.get("raw")
    if not isinstance(raw_b64, str) or not raw_b64.strip():
        return None
    try:
        raw_bytes = decode_gmail_raw_b64(raw_b64)
    except Exception as e:
        logger.debug("Gmail raw decode failed: %s", e)
        return None
    try:
        parsed = message_from_bytes(raw_bytes, policy=email_policy)
    except Exception as e:
        logger.debug("Gmail RFC822 parse failed: %s", e)
        return None

    from_hdr = parsed.get("From") or ""
    from_name, addr = parseaddr(from_hdr)
    addr = (addr or "").strip().lower()
    if not addr or "@" not in addr:
        return None
    user_id = addr

    plain, html_content = _collect_text_parts(parsed)
    utterance = (plain or "").strip()
    if not utterance and html_content:
        utterance = strip_html_to_text(html_content)
    subject = (parsed.get("Subject") or "").strip()
    if not utterance:
        if subject:
            utterance = f"(no body) {subject}"
        else:
            return None

    mid_hdr = (parsed.get("Message-ID") or parsed.get("Message-Id") or "").strip()
    irt = (parsed.get("In-Reply-To") or "").strip()
    to_hdr = (parsed.get("To") or "").strip()

    email_attachments: List[Dict[str, Any]] = []
    if parsed.is_multipart():
        for part in parsed.walk():
            disp = (part.get_content_disposition() or "").lower()
            if disp != "attachment":
                continue
            fn = part.get_filename()
            if fn:
                email_attachments.append({"filename": fn})

    fn_strip = (from_name or "").strip() or None
    data_dict: Dict[str, Any] = {
        "email_provider": "gmail",
        "email_inbound": {
            "MessageId": _normalize_msg_id(mid_hdr) if mid_hdr else message_resource.get("id"),
            "Subject": subject,
            "InReplyTo": _normalize_msg_id(irt) if irt else None,
            "To": to_hdr or None,
            "FromName": fn_strip,
            "GmailMessageId": message_resource.get("id"),
            "GmailThreadId": message_resource.get("threadId"),
        },
    }
    if email_attachments:
        data_dict["email_attachments"] = email_attachments

    return user_id, utterance, data_dict
