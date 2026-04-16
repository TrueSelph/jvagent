"""Gmail API email provider (OAuth via GoogleGmailAction)."""

from __future__ import annotations

import base64
import logging
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import TYPE_CHECKING, Any, Dict, List

from jvagent.action.email_action.email_payload import CanonicalSendMessage, EmailRecipient

from .base import default_inbound_webhook_unsupported

if TYPE_CHECKING:
    from jvagent.action.google.google_gmail_action.google_gmail_action import (
        GoogleGmailAction,
    )

logger = logging.getLogger(__name__)


def _build_rfc822_root(msg: CanonicalSendMessage) -> Any:
    """Build a root ``email.message.Message`` for Gmail ``raw`` send."""
    html_content = msg.html_content
    text_content = msg.text_content
    if not html_content and not text_content:
        raise ValueError("html_content or text_content required")

    if html_content and text_content:
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(str(text_content), "plain", "utf-8"))
        alt.attach(MIMEText(str(html_content), "html", "utf-8"))
        body_container: Any = alt
    elif html_content:
        body_container = MIMEText(str(html_content), "html", "utf-8")
    else:
        body_container = MIMEText(str(text_content), "plain", "utf-8")

    attachments = msg.attachments or []
    if attachments:
        outer = MIMEMultipart("mixed")
        outer.attach(body_container)
        for att in attachments:
            maintype, _, subtype = (
                att.content_type or "application/octet-stream"
            ).partition("/")
            if not subtype:
                subtype = "octet-stream"
            part = MIMEBase(maintype or "application", subtype)
            try:
                raw = base64.b64decode(att.content_base64, validate=False)
            except Exception as e:
                raise ValueError(f"Invalid attachment base64: {att.filename!r}") from e
            part.set_payload(raw)
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                att.disposition or "attachment",
                filename=att.filename,
            )
            if att.content_id:
                part.add_header("Content-ID", f"<{att.content_id.strip('<>')}>")
            outer.attach(part)
        final = outer
    else:
        final = body_container

    final["From"] = formataddr((msg.sender_name or "", msg.sender_email))
    final["To"] = formataddr((msg.to_name or "", msg.to_email))
    final["Subject"] = msg.subject
    cc_list: List[EmailRecipient] = list(msg.cc or [])
    if cc_list:
        cc_parts: List[str] = []
        to_lower = (msg.to_email or "").strip().lower()
        for r in cc_list:
            addr = (r.email or "").strip()
            if not addr or "@" not in addr or addr.lower() == to_lower:
                continue
            cc_parts.append(formataddr((r.name or "", addr)))
        if cc_parts:
            final["Cc"] = ", ".join(cc_parts)
    if msg.reply_to and str(msg.reply_to).strip():
        final["Reply-To"] = str(msg.reply_to).strip()
    if msg.headers:
        for k, v in msg.headers.items():
            if str(k).lower() in ("from", "to", "subject", "reply-to", "cc"):
                continue
            final[str(k)] = str(v)
    return final


class GmailEmailProvider:
    """Send via Gmail API using a linked ``GoogleGmailAction`` (user OAuth)."""

    def __init__(self, *, gmail_action: "GoogleGmailAction") -> None:
        self._gmail = gmail_action

    async def send_canonical(self, msg: CanonicalSendMessage) -> Dict[str, Any]:
        try:
            root = _build_rfc822_root(msg)
            raw = base64.urlsafe_b64encode(root.as_bytes()).decode()
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.exception("Gmail MIME build failed")
            return {"ok": False, "error": str(e)}

        try:
            logger.info(
                "Gmail send_canonical: from=%r to=%r subject=%r",
                msg.sender_email,
                msg.to_email,
                msg.subject,
            )
            service = await self._gmail.get_service()
            result = (
                service.users()
                .messages()
                .send(userId="me", body={"raw": raw})
                .execute()
            )
        except Exception as e:
            logger.error(
                "Gmail send failed from=%r to=%r: %s",
                msg.sender_email,
                msg.to_email,
                e,
                exc_info=True,
            )
            return {"ok": False, "error": str(e)}
        out: Dict[str, Any] = {"ok": True}
        if isinstance(result, dict):
            out.update(result)
        return out

    async def create_inbound_webhook(
        self,
        *,
        url: str,
        domain: str,
        description: str = "",
    ) -> Dict[str, Any]:
        _ = (url, domain, description)
        return default_inbound_webhook_unsupported()
