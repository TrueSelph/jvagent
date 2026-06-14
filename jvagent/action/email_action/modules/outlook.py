"""Microsoft Graph Outlook email provider (OAuth via MicrosoftOutlookMailAction)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List

from jvagent.action.email_action.email_payload import CanonicalSendMessage

from .base import default_inbound_webhook_unsupported

if TYPE_CHECKING:
    from jvagent.action.microsoft.microsoft_outlook_mail_action.microsoft_outlook_mail_action import (
        MicrosoftOutlookMailAction,
    )

logger = logging.getLogger(__name__)


def _build_graph_message(msg: CanonicalSendMessage) -> Dict[str, Any]:
    html_content = msg.html_content
    text_content = msg.text_content
    if not html_content and not text_content:
        raise ValueError("html_content or text_content required")

    if html_content:
        body: Dict[str, str] = {
            "contentType": "HTML",
            "content": str(html_content),
        }
    else:
        body = {
            "contentType": "Text",
            "content": str(text_content or ""),
        }

    to_addr: Dict[str, Any] = {"address": msg.to_email}
    if msg.to_name and str(msg.to_name).strip():
        to_addr["name"] = str(msg.to_name).strip()

    graph_msg: Dict[str, Any] = {
        "subject": msg.subject,
        "body": body,
        "toRecipients": [{"emailAddress": to_addr}],
    }

    cc_rows: List[Dict[str, Any]] = []
    to_lower = (msg.to_email or "").strip().lower()
    for r in msg.cc or []:
        addr = (r.email or "").strip()
        if not addr or "@" not in addr or addr.lower() == to_lower:
            continue
        cc_ea: Dict[str, Any] = {"address": addr}
        if r.name and str(r.name).strip():
            cc_ea["name"] = str(r.name).strip()
        cc_rows.append({"emailAddress": cc_ea})
    if cc_rows:
        graph_msg["ccRecipients"] = cc_rows

    if msg.reply_to and str(msg.reply_to).strip():
        graph_msg["replyTo"] = [
            {"emailAddress": {"address": str(msg.reply_to).strip()}}
        ]

    internet_headers: List[Dict[str, str]] = []
    if msg.headers:
        for k, v in msg.headers.items():
            lk = str(k).lower()
            if lk in ("in-reply-to", "references"):
                internet_headers.append({"name": str(k), "value": str(v).strip()})
    if internet_headers:
        graph_msg["internetMessageHeaders"] = internet_headers

    attachments = msg.attachments or []
    if attachments:
        att_payloads: List[Dict[str, Any]] = []
        for att in attachments:
            b64 = "".join(str(att.content_base64).split())
            att_payloads.append(
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": att.filename,
                    "contentType": att.content_type or "application/octet-stream",
                    "contentBytes": b64,
                }
            )
        graph_msg["attachments"] = att_payloads

    return graph_msg


class OutlookEmailProvider:
    """Send via Microsoft Graph using a linked ``MicrosoftOutlookMailAction``."""

    def __init__(self, *, outlook_action: "MicrosoftOutlookMailAction") -> None:
        self._outlook = outlook_action

    async def send_canonical(self, msg: CanonicalSendMessage) -> Dict[str, Any]:
        try:
            graph_msg = _build_graph_message(msg)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.exception("Outlook MIME/graph message build failed")
            return {"ok": False, "error": str(e)}

        payload = {"message": graph_msg, "saveToSentItems": True}
        try:
            logger.info(
                "Outlook send_canonical: to=%r subject=%r",
                msg.to_email,
                msg.subject,
            )
            await self._outlook.graph_json(
                "POST", "/me/sendMail", json_body=payload, ok=(202,)
            )
        except Exception as e:
            logger.error(
                "Outlook send failed to=%r: %s",
                msg.to_email,
                e,
                exc_info=True,
            )
            return {"ok": False, "error": str(e)}
        return {"ok": True}

    async def create_inbound_webhook(
        self,
        *,
        url: str,
        domain: str,
        description: str = "",
    ) -> Dict[str, Any]:
        _ = (url, domain, description)
        return default_inbound_webhook_unsupported()
