"""API endpoints for TTS action.

This module defines all HTTP endpoints for the TTS action.
Endpoints are automatically discovered when this module is imported.
"""

import logging
from typing import Any, Dict

from jvspatial.api import endpoint
from jvspatial.api.exceptions import ResourceNotFoundError

from .base import BaseTTSAction

logger = logging.getLogger(__name__)


async def _get_tts_action(action_id: str):
    """Resolve action by ID; validate it is a BaseTTSAction."""
    action = await BaseTTSAction.get(action_id)
    if action and isinstance(action, BaseTTSAction):
        return action
    return None


@endpoint(
    "/actions/{action_id}/tts/synthesize",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["TTS Action"],
)
async def synthesize_speech(action_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Synthesize speech from text.

    Args:
        action_id: ID of the TTS action instance
        data: Request data containing text and output format options

    Returns:
        Speech synthesis result
    """
    action = await _get_tts_action(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"TTS action with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    text = data.get("text")
    if not text:
        return {"success": False, "error": "Text is required for synthesis"}

    as_base64 = data.get("as_base64", False)
    as_url = data.get("as_url", True)

    result = await action.invoke(text, as_base64=as_base64, as_url=as_url)

    provider = action.get_class_name()
    model = getattr(action, "model", None)
    voice = getattr(action, "voice", None)

    return {
        "success": result is not None,
        "audio": result,
        "format": "base64" if as_base64 else ("url" if as_url else "bytes"),
        "provider": provider,
        "model": model,
        "voice": voice,
    }


@endpoint(
    "/actions/{action_id}/tts/voices",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["TTS Action"],
)
async def get_voices(action_id: str) -> Dict[str, Any]:
    """Get available voices for TTS.

    Args:
        action_id: ID of the TTS action instance

    Returns:
        List of available voices
    """
    action = await _get_tts_action(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"TTS action with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    voices = await action.get_voices()

    provider = action.get_class_name()
    voice = getattr(action, "voice", None)

    return {
        "voices": voices,
        "provider": provider,
        "current_voice": voice,
    }


@endpoint(
    "/actions/{action_id}/tts/models",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["TTS Action"],
)
async def get_models(action_id: str) -> Dict[str, Any]:
    """Get available models for TTS.

    Args:
        action_id: ID of the TTS action instance

    Returns:
        List of available models
    """
    action = await _get_tts_action(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"TTS action with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    models = await action.get_models()

    provider = action.get_class_name()
    model = getattr(action, "model", None)

    return {
        "models": models,
        "provider": provider,
        "current_model": model,
    }


@endpoint(
    "/actions/{action_id}/tts/health",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["TTS Action"],
)
async def tts_health_check(action_id: str) -> Dict[str, Any]:
    """Check TTS service health.

    Args:
        action_id: ID of the TTS action instance

    Returns:
        Health check result
    """
    action = await _get_tts_action(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"TTS action with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    health = await action.healthcheck()

    provider = action.get_class_name()
    model = getattr(action, "model", None)
    voice = getattr(action, "voice", None)

    return {
        "healthy": health is True,
        "details": health if health is not True else None,
        "provider": provider,
        "model": model,
        "voice": voice,
    }
