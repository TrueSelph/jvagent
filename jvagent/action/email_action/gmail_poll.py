"""Poll Gmail inbox for EmailAction (no Pub/Sub)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

from jvspatial import create_task

from jvagent.action.access_control.access_control_action import log_access_denied
from jvagent.action.email_action.email_webhook_helpers import (
    DEFAULT_EMAIL_UTTERANCE_MAX,
    process_email_interaction_async,
)
from jvagent.action.email_action.inbound.gmail import gmail_raw_message_to_tuple

if TYPE_CHECKING:
    from jvagent.action.email_action.email_action import EmailAction
    from jvagent.action.google.google_gmail_action.google_gmail_action import (
        GoogleGmailAction,
    )
    from jvagent.core.agent import Agent

logger = logging.getLogger(__name__)


async def poll_gmail_inbox_once(
    email_action: "EmailAction",
    *,
    agent: Optional["Agent"] = None,
) -> Dict[str, Any]:
    """List messages, process the first that passes access control; mark it read first.

    Returns a small status dict for logging and HTTP responses.
    """
    out: Dict[str, Any] = {
        "scanned": 0,
        "processed": False,
        "skipped_no_access": 0,
        "skipped_parse": 0,
        "error": None,
    }
    prov = (email_action.provider or "gmail").strip().lower()
    if prov != "gmail":
        out["error"] = "not_gmail_provider"
        return out

    gmail = await email_action.get_linked_gmail_action()
    if not gmail:
        out["error"] = "no_google_gmail_action"
        return out

    ag = agent
    if ag is None:
        ag = await email_action.get_agent()
    if not ag:
        out["error"] = "no_agent"
        return out

    agent_id = str(ag.id)
    query = (email_action.gmail_list_query or "is:unread in:inbox").strip()
    max_results = int(email_action.gmail_list_max_results or 25)
    max_results = max(1, min(max_results, 100))

    try:
        stubs = await gmail.list_messages(
            query=query, max_results=max_results, user_id="me"
        )
    except Exception as e:
        logger.error("Gmail list_messages failed: %s", e, exc_info=True)
        out["error"] = str(e)
        return out

    access_control_action = await ag.get_access_control_action()
    utterance_max = int(
        getattr(email_action, "utterance_max_length", None)
        or DEFAULT_EMAIL_UTTERANCE_MAX
    )

    for stub in stubs:
        if not isinstance(stub, dict):
            continue
        mid = stub.get("id")
        if not mid or not isinstance(mid, str):
            continue
        out["scanned"] += 1
        try:
            resource = await gmail.get_message(mid, user_id="me", fmt="raw")
        except Exception as e:
            logger.warning("Gmail get_message %s failed: %s", mid, e)
            out["skipped_parse"] += 1
            continue

        parsed = gmail_raw_message_to_tuple(resource)
        if not parsed:
            out["skipped_parse"] += 1
            continue

        user_id, utterance, data_dict = parsed
        if len(utterance) > utterance_max:
            logger.warning(
                "Gmail message %s utterance too long (%s chars); skipping",
                mid,
                len(utterance),
            )
            out["skipped_parse"] += 1
            continue

        has_access = True
        if access_control_action:
            has_access = await access_control_action.has_action_access(
                user_id=user_id,
                action_label="EmailAction",
                channel="email",
            )
        if not has_access:
            log_access_denied(
                agent_id=agent_id,
                user_id=user_id,
                channel="email",
                action_label="EmailAction",
                stage="email_gmail_poll",
            )
            out["skipped_no_access"] += 1
            continue

        try:
            await gmail.mark_read(mid, user_id="me")
        except Exception as e:
            logger.error("Gmail mark_read %s failed: %s", mid, e, exc_info=True)
            out["error"] = str(e)
            return out

        inbound = data_dict.get("email_inbound") or {}
        sender_name = inbound.get("FromName") if isinstance(inbound, dict) else None

        task = await create_task(
            process_email_interaction_async(
                utterance,
                user_id,
                agent_id,
                ag,
                data_dict,
                sender_name=sender_name,
            ),
            name=f"email_gmail_poll_{user_id}",
        )
        if task is None:
            await process_email_interaction_async(
                utterance,
                user_id,
                agent_id,
                ag,
                data_dict,
                sender_name=sender_name,
            )

        out["processed"] = True
        out["message_id"] = mid
        return out

    return out
