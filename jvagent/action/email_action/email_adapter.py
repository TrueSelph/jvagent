"""Email channel adapter (ResponseBus -> provider send)."""

import logging
from typing import Any, Dict, Optional, Tuple

from jvagent.action.email_action.email_action import EmailAction
from jvagent.action.email_action.email_payload import (
    CanonicalSendMessage,
    normalize_attachments_from_body,
)
from jvagent.action.response.channel_adapter import ChannelAdapter
from jvagent.action.response.message import ResponseMessage

logger = logging.getLogger(__name__)


def _subject_and_thread_headers(
    meta: Dict[str, Any],
) -> Tuple[str, Optional[Dict[str, str]]]:
    """Build reply subject and RFC-style headers from persona publish metadata."""
    inbound = meta.get("email_inbound")
    if not isinstance(inbound, dict):
        inbound = {}
    orig_subj = (inbound.get("Subject") or "").strip()
    explicit = (meta.get("subject") or "").strip()

    if explicit:
        subject = explicit
    elif orig_subj:
        low = orig_subj.lower()
        subject = orig_subj if low.startswith("re:") else f"Re: {orig_subj}"
    else:
        subject = "Message"

    headers: Optional[Dict[str, str]] = None
    mid = (inbound.get("MessageId") or "").strip()
    if mid:
        irt = (inbound.get("InReplyTo") or "").strip()
        references = f"{irt} {mid}".strip() if irt else mid
        headers = {"In-Reply-To": mid, "References": references}
    return subject, headers


class EmailAdapter(ChannelAdapter):
    """Deliver ``ResponseMessage`` via ``EmailAction`` provider."""

    def __init__(self, action: Any) -> None:
        super().__init__(channel="email")
        self.action = action

    async def send(self, message: ResponseMessage) -> bool:
        if not self.action or not self.action.is_configured():
            logger.debug("EmailAdapter: EmailAction not configured")
            return False
        to_email = (message.user_id or "").strip()
        if not to_email or "@" not in to_email:
            logger.error(
                "EmailAdapter: user_id must be a recipient email, got %r", to_email
            )
            return False

        meta = dict(message.metadata or {})
        subject, thread_headers = _subject_and_thread_headers(meta)
        reply_to = (meta.get("reply_to") or "").strip() or None

        raw_html = meta.get("html_content")
        body = (message.content or "").strip()
        if raw_html:
            html_content = str(raw_html)
            text_content = (meta.get("text_content") or "").strip() or None
        elif body:
            if meta.get("email_wrapped_html") or meta.get("email_html"):
                html_content = body
                text_content = None
            else:
                html_content = None
                text_content = body
        else:
            logger.debug("EmailAdapter: empty body for %s", to_email)
            return False

        self.action._apply_env_defaults()
        sender_email, sender_name = await self.action.resolve_outbound_sender()
        if not sender_email:
            logger.error(
                "EmailAdapter: no sender email (set EMAIL_DEFAULT_SENDER or complete Gmail OAuth)"
            )
            return False
        to_name = (meta.get("to_name") or "").strip() or None

        att_raw = meta.get("attachments") or meta.get("email_attachments")
        attachments = normalize_attachments_from_body(att_raw)

        canonical = CanonicalSendMessage(
            to_email=to_email,
            to_name=to_name,
            subject=subject,
            html_content=html_content,
            text_content=text_content,
            sender_email=sender_email,
            sender_name=sender_name,
            reply_to=reply_to,
            headers=thread_headers,
            attachments=attachments,
        )

        try:
            provider = await self.action.api()
            result = await provider.send_canonical(canonical)
            if not result.get("ok"):
                logger.error(
                    "EmailAdapter: send failed for %s: %s",
                    to_email,
                    result.get("error"),
                )
                return False
            return True
        except Exception as e:
            logger.error(
                "EmailAdapter: send error for %s: %s",
                to_email,
                e,
                exc_info=True,
            )
            return False
