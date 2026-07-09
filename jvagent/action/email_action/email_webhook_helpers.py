"""Inbound email webhook helpers (walker + interaction lifecycle)."""

import logging
from typing import Any, Dict, List, Optional, Tuple

from jvspatial.exceptions import DatabaseError

from jvagent.action.access_control.access_control_action import AccessControlAction
from jvagent.action.email_action.email_utterance import (
    build_email_interaction_utterance,
)
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.interact.webhook_pipeline import (
    finalize_interaction_from_webhook,
    get_conversation_with_lock,
)

__all__ = [
    "parse_inbound_payload",
    "create_email_walker",
    "finalize_email_interaction",
    "process_email_interaction_async",
    "inbound_email_access_allowed",
    "inbound_email_access_denied_action",
    "DEFAULT_EMAIL_UTTERANCE_MAX",
    "EMAIL_UTTERANCE_MAX",
]

logger = logging.getLogger(__name__)

DEFAULT_EMAIL_UTTERANCE_MAX = 500_000
EMAIL_UTTERANCE_MAX = DEFAULT_EMAIL_UTTERANCE_MAX


async def inbound_email_access_denied_action(
    access_control_action: Optional[AccessControlAction],
    user_id: str,
) -> Optional[str]:
    """Return the first failing gate label, or None if inbound email is allowed.

    Requires both **EmailAction** and **interact** on channel **email** when access
    control applies, matching :meth:`InteractWalker.interact_init_bootstrap`.
    """
    if not access_control_action:
        return None
    if not await access_control_action.has_action_access(
        user_id=user_id,
        action_label="EmailAction",
        channel="email",
    ):
        return "EmailAction"
    if not await access_control_action.has_action_access(
        user_id=user_id,
        action_label="interact",
        channel="email",
    ):
        return "interact"
    return None


async def inbound_email_access_allowed(
    access_control_action: Optional[AccessControlAction],
    user_id: str,
) -> bool:
    """True if the sender may receive inbound email processing (both gates pass)."""
    return (
        await inbound_email_access_denied_action(access_control_action, user_id)
    ) is None


def parse_inbound_payload(
    provider: str, payload: Any
) -> List[Tuple[str, str, Dict[str, Any]]]:
    """Legacy helper: webhook parsing is implemented in ``endpoints`` (SendGrid)."""
    _ = (provider, payload)
    return []


async def create_email_walker(
    agent_id: str,
    utterance: str,
    sender_email: str,
    data_dict: Dict[str, Any],
    sender_name: Optional[str] = None,
) -> Optional[InteractWalker]:
    """Create an InteractWalker for email (sender email as user id)."""
    try:
        convo_obj = await get_conversation_with_lock(sender_email)

        if convo_obj and getattr(convo_obj, "session_id", None):
            return InteractWalker(
                agent_id=agent_id,
                utterance=utterance,
                channel="email",
                data=data_dict,
                session_id=convo_obj.session_id,
                user_id=sender_email,
                user_name=sender_name,
                stream=False,
            )
        return InteractWalker(
            agent_id=agent_id,
            utterance=utterance,
            channel="email",
            data=data_dict,
            user_id=sender_email,
            user_name=sender_name,
            stream=False,
        )
    except Exception as e:
        logger.error("Error creating email walker for %s: %s", sender_email, e)
        return None


async def finalize_email_interaction(
    walker: InteractWalker,
    agent_id: str,
    sender: str,
) -> None:
    """Close interaction, flush, usage, log (Messenger parity)."""
    await finalize_interaction_from_webhook(walker, agent_id, sender)


async def process_email_interaction_async(
    sender: str,
    agent_id: str,
    agent: Any,
    data_dict: Dict[str, Any],
    sender_name: Optional[str] = None,
) -> None:
    """Background: compose/truncate utterance, ensure adapter, spawn walker, finalize."""
    email_action: Any = None
    try:
        email_action = await agent.get_action_by_type("EmailAction")
        if email_action:
            reg_ok = await email_action.ensure_adapter_registered()
            logger.info(
                "process_email_interaction_async: agent_id=%s sender=%r "
                "email_adapter_registered=%s",
                agent_id,
                sender,
                reg_ok,
            )
    except Exception as e:
        logger.warning("Email adapter ensure failed for agent %s: %s", agent_id, e)

    max_chars = int(
        (getattr(email_action, "utterance_max_length", None) if email_action else None)
        or DEFAULT_EMAIL_UTTERANCE_MAX
    )
    try:
        utterance = await build_email_interaction_utterance(
            data_dict, agent=agent, final_max_chars=max_chars
        )
    except Exception as e:
        logger.error(
            "Email utterance build failed for %s: %s", sender, e, exc_info=True
        )
        return

    try:
        walker = await create_email_walker(
            agent_id, utterance, sender, data_dict, sender_name=sender_name
        )
        if not walker:
            return
        await walker.spawn(agent)
        await finalize_email_interaction(walker, agent_id, sender)
    except DatabaseError:
        raise
    except Exception as e:
        logger.error("Error in email interaction for %s: %s", sender, e, exc_info=True)
