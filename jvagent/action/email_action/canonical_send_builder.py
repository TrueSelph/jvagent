"""Build CanonicalSendMessage from HTTP JSON (shared by EmailAction and standalone mail actions)."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from jvspatial.api.exceptions import ValidationError

from jvagent.action.email_action.email_action import EmailAction

from .email_payload import CanonicalSendMessage, normalize_attachments_from_body


async def resolve_outbound_sender_for_standalone_mailbox(
    mailbox_action: Any,
) -> Tuple[str, Optional[str]]:
    """Env default sender, else OAuth mailbox profile ``emailAddress`` (Gmail or Outlook)."""
    env_e = EmailAction._env_default_sender()
    name = EmailAction._env_default_sender_name() or None
    if env_e:
        return env_e, name
    try:
        prof = await mailbox_action.get_profile()
        e = (prof.get("emailAddress") or "").strip()
        if e:
            return e, name
    except Exception:
        pass
    return "", name


async def build_canonical_send_message(
    data: Dict[str, Any],
    *,
    action_id: str,
    resolve_sender: Callable[[], Awaitable[Tuple[str, Optional[str]]]],
    effective_sender_name: Callable[[], Optional[str]],
) -> CanonicalSendMessage:
    """Parse and validate canonical send body; resolve From address when omitted.

    Same rules as ``EmailAction`` email/send (excluding SendGrid raw ``mail``).
    """
    to = (data.get("to") or "").strip()
    if not to or "@" not in to:
        raise ValidationError(
            message="Field 'to' must be a valid email address",
            details={"action_id": action_id},
        )
    subject = (data.get("subject") or "").strip() or "Message"
    html_content = data.get("htmlContent") or data.get("html_content")
    text_content = data.get("textContent") or data.get("text_content")
    if html_content:
        html_content = str(html_content)
    if text_content:
        text_content = str(text_content)
    if not html_content and not text_content:
        raise ValidationError(
            message="Provide htmlContent or textContent (or mail for SendGrid)",
            details={"action_id": action_id},
        )

    sender_email = (data.get("sender_email") or "").strip()
    sender_name = data.get("sender_name")
    if not sender_email:
        resolved_email, resolved_name = await resolve_sender()
        sender_email = resolved_email
        if sender_name is None:
            sender_name = resolved_name
    if not sender_email:
        raise ValidationError(
            message=(
                "sender_email, EMAIL_DEFAULT_SENDER, or OAuth mailbox profile address is required "
                "(Gmail or Outlook)"
            ),
            details={"action_id": action_id},
        )

    to_name = data.get("to_name")
    if to_name is not None:
        to_name = str(to_name).strip() or None
    reply_to = data.get("reply_to")
    if reply_to is not None:
        reply_to = str(reply_to).strip() or None

    if sender_name is None:
        sender_name = effective_sender_name()
    elif isinstance(sender_name, str):
        sender_name = sender_name.strip() or None

    headers = data.get("headers")
    if headers is not None:
        if not isinstance(headers, dict):
            raise ValidationError(
                message="headers must be an object with string values",
                details={"action_id": action_id},
            )
        headers = {str(k): str(v) for k, v in headers.items()}
    else:
        headers = None

    attachments = normalize_attachments_from_body(data.get("attachments"))

    return CanonicalSendMessage(
        to_email=to,
        to_name=to_name,
        subject=subject,
        html_content=html_content,
        text_content=text_content,
        sender_email=sender_email,
        sender_name=sender_name,
        reply_to=reply_to,
        headers=headers,
        attachments=attachments,
    )


def standalone_mailbox_effective_sender_name() -> Optional[str]:
    """Display name from env when no ``sender_name`` in body (standalone mail actions)."""
    return EmailAction._env_default_sender_name() or None
