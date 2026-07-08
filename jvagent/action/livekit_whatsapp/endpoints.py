"""HTTP endpoints for LiveKit WhatsApp call action."""

import logging
from typing import Any, Dict

from fastapi import HTTPException
from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError

from .livekit_whatsapp_action import LiveKitWhatsAppAction

logger = logging.getLogger(__name__)


async def _get_livekit_action(action_id: str) -> LiveKitWhatsAppAction:
    action = await LiveKitWhatsAppAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Action with ID '{action_id}' not found",
            details={"action_id": action_id},
        )
    if not isinstance(action, LiveKitWhatsAppAction):
        raise ResourceNotFoundError(
            message="Action is not a LiveKitWhatsAppAction",
            details={"action_id": action_id},
        )
    return action


@endpoint(
    "/actions/{action_id}/livekit/status",
    methods=["GET"],
    auth=True,
    tags=["Action"],
    response=success_response(
        data={
            "configured": ResponseField(
                field_type=bool,
                description="Whether LiveKit credentials are configured",
                example=True,
            ),
            "agent_name": ResponseField(
                field_type=str,
                description="LiveKit agent dispatch name",
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
async def livekit_whatsapp_status(action_id: str) -> Dict[str, Any]:
    """Return LiveKit WhatsApp call configuration status."""
    action = await _get_livekit_action(action_id)
    return {
        "configured": action.is_configured(),
        "agent_name": action.agent_name,
        "active_calls": len(action._active_calls),
        "cloud_api_version": action.cloud_api_version,
    }
