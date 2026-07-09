"""HTTP endpoints for WhatsApp voice call action."""

import logging
from typing import Any, Dict

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError

from .whatsapp_voice_action import WhatsAppVoiceAction

logger = logging.getLogger(__name__)


async def _get_voice_action(action_id: str) -> WhatsAppVoiceAction:
    action = await WhatsAppVoiceAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Action with ID '{action_id}' not found",
            details={"action_id": action_id},
        )
    if not isinstance(action, WhatsAppVoiceAction):
        raise ResourceNotFoundError(
            message="Action is not a WhatsAppVoiceAction",
            details={"action_id": action_id},
        )
    return action


@endpoint(
    "/actions/{action_id}/voice/status",
    methods=["GET"],
    auth=True,
    tags=["Action"],
    response=success_response(
        data={
            "configured": ResponseField(
                field_type=bool,
                description="Whether jvvoice delegation is configured",
                example=True,
            ),
            "agent_name": ResponseField(
                field_type=str,
                description="jvvoice worker registration name",
                example="jvvoice",
            ),
            "active_calls": ResponseField(
                field_type=int,
                description="Number of calls tracked in this process",
                example=0,
            ),
        }
    ),
)
async def whatsapp_voice_status(action_id: str) -> Dict[str, Any]:
    """Return WhatsApp voice call configuration status."""
    action = await _get_voice_action(action_id)
    return {
        "configured": action.is_configured(),
        "agent_name": action.agent_name,
        "active_calls": len(action._active_calls),
        "cloud_api_version": action.cloud_api_version,
    }
