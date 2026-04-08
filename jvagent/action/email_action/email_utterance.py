"""Compose full email-channel interaction utterance (subject + body)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from jvagent.action.email_action.inbound.text_utils import strip_html_to_text
from jvagent.memory.conversation import Conversation


def _interact_max_utterance_length() -> Optional[int]:
    """Same limit as ``/agents/{id}/interact`` (``InteractRateLimiter.max_utterance_length``)."""
    from jvagent.action.interact.endpoints import _initialize_rate_limiter_from_config
    from jvagent.action.interact.rate_limiter import get_rate_limiter

    _initialize_rate_limiter_from_config()
    return get_rate_limiter().max_utterance_length


def extract_email_body_plain(inbound: Dict[str, Any]) -> str:
    """Plain body from inbound metadata: BodyPlain, else stripped BodyHtml."""
    if not isinstance(inbound, dict):
        return ""
    body_text = (inbound.get("BodyPlain") or "").strip()
    if not body_text:
        html_body = (inbound.get("BodyHtml") or "").strip()
        if html_body:
            body_text = strip_html_to_text(html_body).strip()
    return body_text


def compose_email_channel_utterance(subject: str, body: str) -> str:
    """Subject line + optional standard email body block."""
    subj = (subject or "").strip() or "(no subject)"
    body = (body or "").strip()
    if not body:
        return subj
    return f"{subj}\n\n---\nEmail body:\n{body}"


async def build_email_interaction_utterance(
    data_dict: Dict[str, Any],
    *,
    agent: Any,
    final_max_chars: Optional[int] = None,
) -> str:
    """Build truncated utterance for email inbound (subject + plain body block).

    When ``final_max_chars`` is set (e.g. ``EmailAction.utterance_max_length``), the
    composed string is truncated to that length. When ``None``, the cap is the
    interact rate limiter max (same as ``/agents/{id}/interact``).
    """
    inbound = data_dict.get("email_inbound") or {}
    if not isinstance(inbound, dict):
        inbound = {}
    subject = (inbound.get("Subject") or "").strip() or "(no subject)"
    body = extract_email_body_plain(inbound)
    if body:
        msl = int(getattr(agent, "max_statement_length", None) or 0)
        cap = msl if msl >= 16_000 else 48_000
        body = await Conversation.truncate_statement(body, cap, interaction=None)
    full = compose_email_channel_utterance(subject, body)
    utterance_max = (
        final_max_chars
        if final_max_chars is not None
        else _interact_max_utterance_length()
    )
    return await Conversation.truncate_statement(
        full, utterance_max, interaction=None
    )
