"""One-shot Outlook inbox fetch for EmailAction (webhook-triggered)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

from jvspatial import create_task

from jvagent.action.access_control.access_control_action import log_access_denied
from jvagent.action.email_action.email_webhook_helpers import (
    process_email_interaction_async,
)
from jvagent.action.email_action.inbound.outlook import graph_message_resource_to_tuple

if TYPE_CHECKING:
    from jvagent.action.email_action.email_action import EmailAction
    from jvagent.core.agent import Agent

logger = logging.getLogger(__name__)


async def fetch_next_outlook_inbox_message(
    email_action: "EmailAction",
    *,
    agent: Optional["Agent"] = None,
) -> Dict[str, Any]:
    """List inbox messages, process the first that passes access control; mark read first."""
    out: Dict[str, Any] = {
        "scanned": 0,
        "processed": False,
        "skipped_no_access": 0,
        "skipped_parse": 0,
        "error": None,
    }
    prov = (email_action.provider or "gmail").strip().lower()
    if prov != "outlook":
        out["error"] = "not_outlook_provider"
        return out

    outlook = await email_action.get_linked_outlook_mail_action()
    if not outlook:
        out["error"] = "no_microsoft_outlook_mail_action"
        return out

    ag = agent
    if ag is None:
        ag = await email_action.get_agent()
    if not ag:
        out["error"] = "no_agent"
        return out

    agent_id = str(ag.id)
    flt = (
        (getattr(email_action, "outlook_mail_filter", None) or "isRead eq false")
        .strip()
    )
    max_results = int(email_action.gmail_list_max_results or 25)
    max_results = max(1, min(max_results, 100))

    try:
        stubs = await outlook.list_inbox_messages(
            odata_filter=flt,
            max_results=max_results,
            user_id="me",
        )
    except Exception as e:
        logger.error("Outlook list_inbox_messages failed: %s", e, exc_info=True)
        out["error"] = str(e)
        return out

    access_control_action = await ag.get_access_control_action()

    for stub in stubs:
        if not isinstance(stub, dict):
            continue
        mid = stub.get("id")
        if not mid or not isinstance(mid, str):
            continue
        out["scanned"] += 1
        try:
            resource = await outlook.get_message(mid, user_id="me")
        except Exception as e:
            logger.warning("Outlook get_message %s failed: %s", mid, e)
            out["skipped_parse"] += 1
            continue

        if not resource:
            out["skipped_parse"] += 1
            continue

        parsed = graph_message_resource_to_tuple(resource)
        if not parsed:
            out["skipped_parse"] += 1
            continue

        user_id, _utterance, data_dict = parsed

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
                stage="email_outlook_inbound",
            )
            out["skipped_no_access"] += 1
            continue

        try:
            await outlook.mark_read(mid, user_id="me")
        except Exception as e:
            logger.error("Outlook mark_read %s failed: %s", mid, e, exc_info=True)
            out["error"] = str(e)
            return out

        inbound = data_dict.get("email_inbound") or {}
        sender_name = inbound.get("FromName") if isinstance(inbound, dict) else None

        task = await create_task(
            process_email_interaction_async(
                user_id,
                agent_id,
                ag,
                data_dict,
                sender_name=sender_name,
            ),
            name=f"email_outlook_inbound_{user_id}",
        )
        if task is None:
            await process_email_interaction_async(
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
