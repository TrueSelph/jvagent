"""API endpoints for STT action.

This module defines all HTTP endpoints for the STT action.
Endpoints are automatically discovered when this module is imported.
"""

import logging
from typing import Any, Dict

from jvspatial.api import endpoint
from jvspatial.api.exceptions import ResourceNotFoundError

from .base import BaseSTTAction

logger = logging.getLogger(__name__)


async def _get_stt_action(action_id: str):
    """Resolve action by ID; validate it is a BaseSTTAction."""
    action = await BaseSTTAction.get(action_id)
    if action and isinstance(action, BaseSTTAction):
        return action
    return None


@endpoint(
    "/actions/{action_id}/stt/transcribe",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["STT Action"],
)
async def transcribe_audio(action_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Transcribe audio to text.

    Args:
        action_id: ID of the STT action instance
        data: Request data containing audio_url or audio_base64

    Returns:
        Transcription result
    """
    action = await _get_stt_action(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"STT action with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    audio_url = data.get("audio_url")
    audio_base64 = data.get("audio_base64")
    audio_type = data.get("audio_type", "audio/mp3")

    if audio_url:
        transcript = await action.invoke(audio_url)
    elif audio_base64:
        transcript = await action.invoke_base64(audio_base64, audio_type)
    else:
        return {
            "success": False,
            "error": "Either audio_url or audio_base64 must be provided",
        }

    provider = action.get_class_name()
    model = getattr(action, "model", None)

    return {
        "success": transcript is not None,
        "transcript": transcript,
        "provider": provider,
        "model": model,
    }


@endpoint(
    "/actions/{action_id}/stt/health",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["STT Action"],
)
async def stt_health_check(action_id: str) -> Dict[str, Any]:
    """Check STT service health.

    Args:
        action_id: ID of the STT action instance

    Returns:
        Health check result
    """
    action = await _get_stt_action(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"STT action with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    health = await action.healthcheck()

    provider = action.get_class_name()
    model = getattr(action, "model", None)

    return {
        "healthy": health is True,
        "details": health if health is not True else None,
        "provider": provider,
        "model": model,
    }
