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

# Match SendGrid inbound inline cap so large images are not loaded into vision prompts.
_MAX_INLINE_IMAGE_BYTES = 256_000


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
    subject = (parsed.get("Subject") or "").strip()

    raw_plain = (plain or "").strip()
    raw_html = (html_content or "").strip() if html_content else ""

    body_plain_for_inbound = raw_plain
    if not body_plain_for_inbound and raw_html:
        body_plain_for_inbound = strip_html_to_text(html_content or "").strip()

    email_attachments: List[Dict[str, Any]] = []
    image_urls: List[str] = []
    if parsed.is_multipart() or parsed.get_content_maintype() == "multipart":
        for part in parsed.walk():
            ctype = part.get_content_type() or ""
            if ctype.startswith("image/"):
                blob = part.get_payload(decode=True)
                if (
                    isinstance(blob, bytes)
                    and 0 < len(blob) <= _MAX_INLINE_IMAGE_BYTES
                ):
                    b64 = base64.standard_b64encode(blob).decode("ascii")
                    image_urls.append(f"data:{ctype};base64,{b64}")

            disp = (part.get_content_disposition() or "").lower()
            if disp == "attachment":
                fn = part.get_filename()
                if fn:
                    email_attachments.append({"filename": fn})
    else:
        ctype = parsed.get_content_type() or ""
        if ctype.startswith("image/"):
            blob = parsed.get_payload(decode=True)
            if (
                isinstance(blob, bytes)
                and 0 < len(blob) <= _MAX_INLINE_IMAGE_BYTES
            ):
                b64 = base64.standard_b64encode(blob).decode("ascii")
                image_urls.append(f"data:{ctype};base64,{b64}")

    has_any_content = bool(subject) or bool(raw_plain) or bool(raw_html)
    has_any_content = has_any_content or bool(image_urls) or bool(email_attachments)
    if not has_any_content:
        return None

    utterance = subject if subject else "(no subject)"

    mid_hdr = (parsed.get("Message-ID") or parsed.get("Message-Id") or "").strip()
    irt = (parsed.get("In-Reply-To") or "").strip()
    to_hdr = (parsed.get("To") or "").strip()

    fn_strip = (from_name or "").strip() or None
    inbound: Dict[str, Any] = {
        "MessageId": _normalize_msg_id(mid_hdr) if mid_hdr else message_resource.get("id"),
        "Subject": subject,
        "InReplyTo": _normalize_msg_id(irt) if irt else None,
        "To": to_hdr or None,
        "FromName": fn_strip,
        "GmailMessageId": message_resource.get("id"),
        "GmailThreadId": message_resource.get("threadId"),
    }
    if raw_plain:
        inbound["BodyPlain"] = raw_plain
    elif body_plain_for_inbound:
        inbound["BodyPlain"] = body_plain_for_inbound
    if raw_html:
        inbound["BodyHtml"] = raw_html

    data_dict: Dict[str, Any] = {
        "email_provider": "gmail",
        "email_inbound": inbound,
    }
    if email_attachments:
        data_dict["email_attachments"] = email_attachments
    if image_urls:
        data_dict["image_urls"] = image_urls

    return user_id, utterance, data_dict
