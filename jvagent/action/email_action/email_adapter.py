"""Email channel adapter (ResponseBus -> provider send).

Mirrors WhatsApp/Messenger adapters: ``send()`` delivers ``adhoc`` messages published
on channel ``email`` with no extra gating (streaming still emits a single final adhoc).
"""

import asyncio
import logging
from email.utils import getaddresses
from typing import Any, Dict, List, Optional, Tuple

from jvagent.action.email_action.email_payload import (
    CanonicalSendMessage,
    EmailRecipient,
    normalize_attachments_from_body,
)
from jvagent.action.response.channel_adapter import ChannelAdapter
from jvagent.action.response.message import ResponseMessage

logger = logging.getLogger(__name__)


def _cc_recipients_from_inbound(
    inbound: Dict[str, Any], to_email: str, sender_email: str
) -> List[EmailRecipient]:
    """Parse ``email_inbound.Cc`` (RFC-style list) into recipients; exclude To and From."""
    raw = inbound.get("Cc")
    if not raw:
        return []
    if isinstance(raw, list):
        hdr = ", ".join(str(x).strip() for x in raw if str(x).strip())
    else:
        hdr = str(raw).strip()
    if not hdr:
        return []
    exclude = {to_email.strip().lower(), (sender_email or "").strip().lower()}
    out: List[EmailRecipient] = []
    seen: set[str] = set()
    for name, addr in getaddresses([hdr]):
        e = (addr or "").strip()
        if not e or "@" not in e:
            continue
        low = e.lower()
        if low in exclude or low in seen:
            continue
        seen.add(low)
        nm = (name or "").strip() or None
        out.append(EmailRecipient(email=e, name=nm))
    return out


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
    parent = (
        (meta.get("email_parent_message_id") or meta.get("parent_message_id") or "")
        .strip()
    )
    mid = parent or (inbound.get("MessageId") or "").strip()
    if mid:
        irt = (inbound.get("InReplyTo") or "").strip()
        references = f"{irt} {mid}".strip() if irt else mid
        headers = {"In-Reply-To": mid, "References": references}
    return subject, headers


class EmailAdapter(ChannelAdapter):
    """Deliver ``ResponseMessage`` via ``EmailAction`` provider (same contract as WhatsApp/Messenger)."""

    def __init__(self, action: Any) -> None:
        super().__init__(channel="email")
        self.action = action
        self._user_locks: Dict[str, asyncio.Lock] = {}

    def _get_user_lock(self, user_id: str) -> asyncio.Lock:
        if user_id not in self._user_locks:
            if len(self._user_locks) >= 1000:
                for key in list(self._user_locks.keys())[:100]:
                    del self._user_locks[key]
            self._user_locks[user_id] = asyncio.Lock()
        return self._user_locks[user_id]

    async def send(self, message: ResponseMessage) -> bool:
        logger.debug(
            "EmailAdapter: send() called message_id=%s session_id=%s interaction_id=%s "
            "message_type=%s",
            message.id,
            message.session_id,
            message.interaction_id,
            message.message_type,
        )

        if not self.action or not self.action.is_configured():
            logger.debug(
                "EmailAdapter: skipping send — EmailAction not configured",
            )
            return False

        to_email = (message.user_id or "").strip()
        if not to_email or "@" not in to_email:
            logger.error(
                "EmailAdapter: cannot send message %s — user_id must be recipient email, got %r",
                message.id,
                to_email,
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
            logger.debug(
                "EmailAdapter: skipping empty body for user_id=%s message_id=%s",
                to_email,
                message.id,
            )
            return False

        if html_content is not None:
            html_content = html_content.rstrip() + "<br/><br/>"
        if text_content is not None:
            text_content = text_content.rstrip() + "\n\n---\n"

        lock = self._get_user_lock(to_email)
        async with lock:
            self.action._apply_env_defaults()
            sender_email, sender_name = await self.action.resolve_outbound_sender()
            if not sender_email:
                logger.error(
                    "EmailAdapter: no sender email for %s (set EMAIL_DEFAULT_SENDER or "
                    "complete mailbox OAuth for Gmail/Outlook)",
                    to_email,
                )
                return False
            to_name = (meta.get("to_name") or "").strip() or None

            att_raw = meta.get("attachments") or meta.get("email_attachments")
            attachments = normalize_attachments_from_body(att_raw)

            inbound_dict = meta.get("email_inbound")
            if not isinstance(inbound_dict, dict):
                inbound_dict = {}
            cc_list = _cc_recipients_from_inbound(inbound_dict, to_email, sender_email)

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
                cc=cc_list,
            )

            prov = (getattr(self.action, "provider", None) or "gmail").strip().lower()
            logger.debug(
                "EmailAdapter: sending provider=%s from=%r to=%r subject=%r",
                prov,
                sender_email,
                to_email,
                subject,
            )

            try:
                provider = await self.action.api()
                result = await provider.send_canonical(canonical)
                if not result.get("ok"):
                    logger.error(
                        "EmailAdapter: send failed provider=%s to=%r subject=%r error=%s",
                        prov,
                        to_email,
                        subject,
                        result.get("error"),
                    )
                    return False
                logger.debug(
                    "EmailAdapter: send ok provider=%s to=%r message_id=%s",
                    prov,
                    to_email,
                    message.id,
                )
                return True
            except Exception as e:
                logger.error(
                    "EmailAdapter: send exception for %s: %s",
                    to_email,
                    e,
                    exc_info=True,
                )
                return False
