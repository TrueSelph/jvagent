"""SendGrid Inbound Parse: multipart or urlencoded form fields."""

from __future__ import annotations

import base64
import json
import logging
import re
from email.utils import parseaddr
from typing import Any, Dict, List, Optional, Tuple

from starlette.datastructures import FormData, UploadFile

logger = logging.getLogger(__name__)

InboundTuple = Tuple[str, str, Dict[str, Any]]

_MAX_INLINE_ATTACHMENT_BYTES = 256_000


def _parse_from_field(from_val: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (email_lower, display_name) from SendGrid ``from`` string."""
    s = (from_val or "").strip()
    if not s:
        return None, None
    name, addr = parseaddr(s)
    addr = (addr or "").strip().lower()
    name = (name or "").strip() or None
    if addr and "@" in addr:
        return addr, name
    if "@" in s:
        return s.lower(), None
    return None, None


def _parse_headers_json(raw: str) -> Dict[str, str]:
    if not raw or not str(raw).strip():
        return {}
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(obj, dict):
        return {}
    out: Dict[str, str] = {}
    for k, v in obj.items():
        if v is None:
            continue
        key = str(k)
        out[key] = str(v)
    return out


def _header_casefold_lookup(headers: Dict[str, str], *names: str) -> str:
    lower = {k.lower(): v for k, v in headers.items()}
    for n in names:
        v = lower.get(n.lower())
        if v:
            return str(v).strip()
    return ""


async def parse_sendgrid_inbound(form: FormData) -> List[InboundTuple]:
    """Convert SendGrid Inbound Parse form to one jvagent tuple (single message per POST)."""

    # FormData.get returns str | UploadFile
    def _get_str(key: str) -> str:
        v = form.get(key)
        if v is None:
            return ""
        if hasattr(v, "read"):
            return ""
        return str(v)

    from_raw = _get_str("from")
    user_id, from_name = _parse_from_field(from_raw)
    if not user_id:
        logger.debug("SendGrid inbound: missing usable from address")
        return []

    subject = _get_str("subject").strip()
    text = _get_str("text").strip()
    html = _get_str("html").strip()

    utterance = text
    if not utterance and html:
        from jvagent.action.email_action.inbound.text_utils import strip_html_to_text

        utterance = strip_html_to_text(html)

    headers_raw = _get_str("headers")
    parsed_headers = _parse_headers_json(headers_raw)
    message_id = _header_casefold_lookup(
        parsed_headers, "Message-ID", "Message-Id", "MessageId"
    )
    in_reply_to = _header_casefold_lookup(parsed_headers, "In-Reply-To", "InReplyTo")

    envelope_raw = _get_str("envelope")
    to_val: Any = None
    if envelope_raw:
        try:
            env = json.loads(envelope_raw)
            if isinstance(env, dict):
                to_val = env.get("to")
        except json.JSONDecodeError:
            pass

    email_attachments: List[Dict[str, Any]] = []
    attachment_info_raw = _get_str("attachment-info")
    if attachment_info_raw:
        try:
            info = json.loads(attachment_info_raw)
            if isinstance(info, dict):
                for fname, meta in info.items():
                    row: Dict[str, Any] = {"filename": fname}
                    if isinstance(meta, dict):
                        row["type"] = meta.get("type") or meta.get("content-type")
                        row["content_id"] = meta.get("content-id") or meta.get(
                            "content_id"
                        )
                    email_attachments.append(row)
        except json.JSONDecodeError:
            pass

    # File parts: attachment1, attachment2, …
    for key in list(form.keys()):
        if not key.startswith("attachment"):
            continue
        if key == "attachment-info" or key == "attachments":
            continue
        val = form.get(key)
        if isinstance(val, UploadFile) and val.filename:
            size = 0
            content_b64: Optional[str] = None
            try:
                data = await val.read()
                size = len(data)
                if size <= _MAX_INLINE_ATTACHMENT_BYTES:
                    content_b64 = base64.b64encode(data).decode("ascii")
            except Exception as e:
                logger.debug("SendGrid inbound: read attachment %s: %s", key, e)
            email_attachments.append(
                {
                    "filename": val.filename,
                    "size": size,
                    "field": key,
                    **({"content_base64": content_b64} if content_b64 else {}),
                }
            )

    if not utterance:
        if subject:
            utterance = f"(no body) {subject}"
        else:
            return []

    email_inbound: Dict[str, Any] = {
        "Subject": subject,
        "MessageId": message_id or None,
        "InReplyTo": in_reply_to or None,
        "To": to_val or _get_str("to") or None,
        "FromName": from_name,
    }
    if text:
        email_inbound["BodyPlain"] = text
    if html:
        email_inbound["BodyHtml"] = html
    if html and not text:
        email_inbound["BodyPlain"] = utterance

    data_dict: Dict[str, Any] = {
        "email_provider": "sendgrid",
        "email_inbound": email_inbound,
    }
    if email_attachments:
        data_dict["email_attachments"] = email_attachments

    return [(user_id, utterance, data_dict)]
