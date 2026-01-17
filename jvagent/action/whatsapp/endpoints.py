"""WhatsApp Action Endpoints."""

import logging
from typing import Any, Dict, Optional


import asyncio
from fastapi import Request
from jvagent.core.agent import Agent
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.memory.conversation import Conversation

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import success_response, ResponseField
from jvspatial.api.exceptions import ResourceNotFoundError

from .whatsapp_action import WhatsAppAction

logger = logging.getLogger(__name__)


@endpoint(
    "/whatsapp/interact/webhook/{agent_id}",
    methods=["POST"],
    auth=False,
    tags=["WhatsApp"],
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="received"),
            "response": ResponseField(field_type=Optional[str], example="Hello!", default=None),
        }
    ),
)
async def whatsapp_interact(request: Request, agent_id: str) -> Dict[str, Any]:
    """WhatsApp Interact Webhook.

    Processes incoming WhatsApp messages and triggers an interaction via InteractWalker.
    Returns immediately with 200 OK and processes the interaction asynchronously.
    """

    # Validate agent exists
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID '{agent_id}' not found",
            details={"agent_id": agent_id},
        )

    action = await agent.get_action_by_type("WhatsAppAction")
    if not action:
        raise ResourceNotFoundError(
            message="Action with label 'WhatsAppAction' not found",
            details={"agent_id": agent_id},
        )

    try:
        request_data = await request.json()
        data = await action.api().parse_inbound_message(request_data)
    except Exception:
        data = {}

    logger.info(f"Received WhatsApp webhook for agent {agent_id}: {data}")

    if not data:
        return {"status": "received", "response": "no content"}

    utterance = data.get("body") or data.get("caption")
    utterance = utterance.strip() if utterance else None
    sender = data.get("sender")

    if not utterance:
        return {"status": "ignored", "response": "no utterance found"}

    # Return immediately with 200 OK
    response = {"status": "received"}

    # Process interaction asynchronously in background
    async def process_interaction():
        """Process the interaction in the background."""
        try:
            # Trigger typing immediately
            await action.set_typing(sender, True)

            convo_obj = await Conversation.find_one({"context.user_id": sender})

            if convo_obj and getattr(convo_obj, "session_id", None):
                walker = InteractWalker(
                    agent_id=agent_id,
                    utterance=utterance,
                    channel="whatsapp",
                    data=data or {},
                    session_id=convo_obj.session_id,
                    stream=False,
                )
            else:
                walker = InteractWalker(
                    agent_id=agent_id,
                    utterance=utterance,
                    channel="whatsapp",
                    data=data or {},
                    user_id=sender,
                    stream=False,
                )
            await walker.spawn(agent)
        except Exception as e:
            logger.error(
                f"Error processing WhatsApp interaction for agent {agent_id}: {e}",
                exc_info=True,
            )

    # Start background task
    asyncio.create_task(process_interaction())

    return response


@endpoint(
    "/actions/{action_id}/send_message",
    methods=["POST"],
    auth=True,
    tags=["WhatsApp"],
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="sent"),
        }
    ),
)
async def whatsapp_send_message(
    action_id: str,
    to: str,
    message: str,
) -> Dict[str, Any]:
    """Send a WhatsApp message via a specific WhatsApp action."""
    action = await WhatsAppAction.get(action_id)
    if not action or not isinstance(action, WhatsAppAction):
        raise ResourceNotFoundError(f"WhatsApp action not found: {action_id}")

    result = await action.send_message(to, message)
    return result
