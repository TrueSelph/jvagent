"""Inbound email webhook helpers (walker + interaction lifecycle)."""

import logging
from typing import Any, Dict, List, Optional, Tuple

from jvspatial.exceptions import DatabaseError

from jvagent.action.email_action.email_utterance import build_email_interaction_utterance
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.whatsapp.utils.endpoint_helpers import get_conversation_with_lock
from jvagent.core.app import App

__all__ = [
    "parse_inbound_payload",
    "create_email_walker",
    "finalize_email_interaction",
    "process_email_interaction_async",
    "DEFAULT_EMAIL_UTTERANCE_MAX",
    "EMAIL_UTTERANCE_MAX",
]

logger = logging.getLogger(__name__)

DEFAULT_EMAIL_UTTERANCE_MAX = 500_000
EMAIL_UTTERANCE_MAX = DEFAULT_EMAIL_UTTERANCE_MAX


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
    interaction = walker.interaction
    if not interaction:
        return

    try:
        await interaction.close_interaction()
        from jvspatial import flush_deferred_entities

        await flush_deferred_entities(interaction, walker.conversation, strict=True)

        from jvagent.action.interact.endpoints import (
            _build_interaction_log_data,
            _finalize_usage,
        )
        from jvagent.logging.service import INTERACTION_LEVEL_NUMBER

        await _finalize_usage(interaction)

        try:
            app = await App.get()
            app_id = app.id if app else ""
            active_tasks = []
            if walker.conversation:
                active_tasks = walker.conversation.get_active_tasks(status="active")
            log_data, message = _build_interaction_log_data(
                interaction,
                app_id,
                agent_id,
                active_tasks=active_tasks,
                visitor_data=walker.data,
            )
            logger.log(INTERACTION_LEVEL_NUMBER, message, extra=log_data)
        except Exception as log_err:
            logger.debug("Email interaction log failed: %s", log_err)

    except DatabaseError as e:
        logger.error(
            "Database error finalizing email interaction for %s: %s",
            sender,
            e,
        )
        raise
    except Exception as e:
        logger.error("Error finalizing email interaction for %s: %s", sender, e)


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
            await email_action.ensure_adapter_registered()
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
        logger.error(
            "Error in email interaction for %s: %s", sender, e, exc_info=True
        )
