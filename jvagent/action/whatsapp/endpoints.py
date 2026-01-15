"""WhatsApp Action Endpoints."""

import logging
from typing import Any, Dict, Optional

from fastapi import Request
from jvagent.action.base import Action
from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import success_response, ResponseField
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError
from jvagent.action.interact.response_builder import build_interact_response


from .whatsapp import Whatsapp
from jvagent.action.response.message import ResponseMessage
from jvagent.memory.conversation import Conversation

logger = logging.getLogger(__name__)
def _build_interaction_log_data(interaction, app_id, agent_id=None):
    """Build comprehensive log data dictionary for interaction logging.
    
    This function extracts all available interaction data and builds a complete
    log payload that includes the full interaction state, metadata, and context.
    
    Args:
        interaction: Interaction node instance
        app_id: Application ID
        agent_id: Optional agent ID
    
    Returns:
        Tuple of (log_data_dict, message_string) for logger.log(INTERACTION_LEVEL_NUMBER, message, extra=log_data)
    """
    # Extract all interaction fields
    interaction_id = interaction.id if hasattr(interaction, "id") else None
    user_id = interaction.user_id if hasattr(interaction, "user_id") else ""
    session_id = interaction.session_id if hasattr(interaction, "session_id") else ""
    conversation_id = (
        interaction.conversation_id if hasattr(interaction, "conversation_id") else ""
    )
    utterance = interaction.utterance if hasattr(interaction, "utterance") else ""
    response = interaction.response if hasattr(interaction, "response") else None
    channel = interaction.channel if hasattr(interaction, "channel") else "default"
    interpretation = (
        interaction.interpretation if hasattr(interaction, "interpretation") else None
    )
    anchors = interaction.anchors if hasattr(interaction, "anchors") else []
    actions = interaction.actions if hasattr(interaction, "actions") else []
    directives = interaction.directives if hasattr(interaction, "directives") else []
    parameters = interaction.parameters if hasattr(interaction, "parameters") else []
    events = interaction.events if hasattr(interaction, "events") else []
    observability_metrics = (
        interaction.observability_metrics
        if hasattr(interaction, "observability_metrics")
        else []
    )
    streamed = interaction.streamed if hasattr(interaction, "streamed") else False
    closed = interaction.closed if hasattr(interaction, "closed") else False
    started_at = (
        interaction.started_at.isoformat()
        if hasattr(interaction, "started_at") and interaction.started_at
        else None
    )
    completed_at = (
        interaction.completed_at.isoformat()
        if hasattr(interaction, "completed_at") and interaction.completed_at
        else None
    )
    
    # Get full interaction state (comprehensive export)
    if hasattr(interaction, "get_state"):
        interaction_data = interaction.get_state()
    else:
        # Fallback: build comprehensive state manually
        interaction_data = {
            "id": interaction_id,
            "conversation_id": conversation_id,
            "user_id": user_id,
            "session_id": session_id,
            "utterance": utterance,
            "channel": channel,
            "response": response,
            "actions": actions,
            "directives": directives,
            "parameters": parameters,
            "events": events,
            "observability_metrics": observability_metrics,
            "interpretation": interpretation,
            "anchors": anchors,
            "started_at": started_at,
            "completed_at": completed_at,
            "closed": closed,
            "streamed": streamed,
        }
    
    # Build message
    message = f"Interaction: {utterance[:100]}" if utterance else "Interaction completed"
    if response:
        message += f" → {response[:100]}"
    
    # Build event code
    event_code = "interaction_completed"
    if closed:
        event_code = "interaction_closed"
    
    # Calculate duration if available
    duration = None
    if hasattr(interaction, "get_duration"):
        duration = interaction.get_duration()
        if duration <= 0:
            duration = None
    
    # Build comprehensive extra dict for logger
    # All fields in 'extra' will be captured by DBLogHandler and stored in log_data
    # Only interaction properties are included (no nested 'details' dict)
    log_data = {
        "event_code": event_code,
        # Core identifiers
        "app_id": app_id,
        "agent_id": agent_id or "",
        "user_id": user_id,
        "session_id": session_id,
        "interaction_id": interaction_id or "",
        "conversation_id": conversation_id,
        # Full interaction payload
        "interaction_data": interaction_data,
        # Interaction properties
        "utterance": utterance,
        "response": response,
        "channel": channel,
        "actions": actions,
        "directives": directives,
        "parameters": parameters,
        "events": events,
        "observability_metrics": observability_metrics,
        "interpretation": interpretation,
        "anchors": anchors,
        "streamed": streamed,
        "closed": closed,
        "has_response": response is not None,
        "action_count": len(actions),
        "started_at": started_at,
        "completed_at": completed_at,
    }
    
    # Add duration if available
    if duration is not None:
        log_data["duration_seconds"] = duration
    
    return log_data, message



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
async def whatsapp_interact_webhook(request: Request, agent_id: str) -> Dict[str, Any]:
    """WhatsApp Interact Webhook.
    
    Processes incoming WhatsApp messages and triggers an interaction via InteractWalker.
    """
    from jvagent.core.agent import Agent
    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.action.interact.response_builder import build_interact_response

    try:
        data = await request.json()
    except Exception:
        data = {}

    logger.info(f"Received WhatsApp webhook for agent {agent_id}: {data}")

    if not data:
         return {
             "status": "received",
             "session_id": None,
             "response": "no content"
         }

    utterance = data.get("body")
    
    if not utterance:
        return {"status": "ignored", "reason": "no utterance found"}

    # Validate agent exists
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID '{agent_id}' not found",
            details={"agent_id": agent_id},
        )


    convo_obj = await Conversation.find_one({"context.user_id": data.get("from")})
    
    if convo_obj and getattr(convo_obj, "session_id", None):
        walker = InteractWalker(
            agent_id=agent_id,
            utterance=utterance.strip(),
            channel="whatsapp",
            data=data or {},
            session_id=convo_obj.session_id,
            stream=False,
        )
    else:
        walker = InteractWalker(
            agent_id=agent_id,
            utterance=utterance.strip(),
            channel="whatsapp",
            data=data or {},
            user_id=data.get("from"),
            stream=False,
        )
    await walker.spawn(agent)
    
    return {"status": "received"}
    # # Get interaction result
    # interaction = walker.interaction
    # report = await walker.get_report()

    # if not interaction:
    #      logger.error("Interaction not created")
    #      return {"status": str(report), "response": "Interaction failed"}
         
    # # Finalize interaction similar to interact endpoint
    # if walker.response_bus:
    #     await walker.response_bus.finalize_interaction(
    #         interaction_id=interaction.id,
    #         interaction=interaction,
    #         session_id=walker.session_id or "",
    #         channel=walker.channel,
    #     )


    # adapter = action.get_adapter()
    
    # # Create ResponseMessage for the adapter
    # msg = ResponseMessage(
    #     session_id=walker.session_id or "",
    #     interaction_id=interaction.id,
    #     message_type="final",
    #     content=interaction.response or "",
    #     channel="whatsapp",
    #     metadata={"recipient": data.get("from")}
    # )
    
    # await adapter.send_to_destination(msg)

    
    # # Build result
    # result = {
    #     "status": "received", 
    #     "session_id": walker.session_id, 
    #     "response": interaction.response
    # }
    # return result


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
    action = await Whatsapp.get(action_id)
    if not action or not isinstance(action, Whatsapp):
        raise ResourceNotFoundError(f"WhatsApp action not found: {action_id}")

    result = await action.send_message(to, message)
    return result