"""API endpoints for STT action.

This module defines all HTTP endpoints for the STT action.
Endpoints are automatically discovered when this module is imported.
"""

import logging
from typing import Any, Dict

from jvspatial.api import endpoint
from jvspatial.api.exceptions import ResourceNotFoundError

from .stt_action import STTAction

logger = logging.getLogger(__name__)


@endpoint(
    "/actions/{action_id}/stt/transcribe",
    methods=["POST"],
    auth=True,
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
    action = await STTAction.get(action_id)
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

    return {
        "success": transcript is not None,
        "transcript": transcript,
        "provider": action.provider,
        "model": action.model,
    }


@endpoint(
    "/actions/{action_id}/stt/health",
    methods=["GET"],
    auth=True,
    tags=["STT Action"],
)
async def stt_health_check(action_id: str) -> Dict[str, Any]:
    """Check STT service health.

    Args:
        action_id: ID of the STT action instance

    Returns:
        Health check result
    """
    action = await STTAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"STT action with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    health = await action.healthcheck()

    return {
        "healthy": health is True,
        "details": health if health is not True else None,
        "provider": action.provider,
        "model": action.model,
    }
