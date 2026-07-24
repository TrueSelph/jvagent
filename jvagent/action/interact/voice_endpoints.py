"""Public voice endpoints for the embeddable messenger (STT + TTS).

Agent-scoped and ``auth=False`` like ``interact``, but always gated by a valid
``X-Session-Token`` (see :mod:`jvagent.action.interact.public_gate`). They reuse
the agent's already-configured provider actions — ``BaseSTTAction`` /
``BaseTTSAction`` — rather than the admin ``/actions/{id}/tts`` routes, which
require admin auth and are unusable by anonymous messenger users.

* ``POST /agents/{agent_id}/voice/stt`` — ``{audio_base64, audio_type}`` →
  ``{transcript}``. The messenger drops the transcript into the composer; the
  message itself still goes through ``/interact``.
* ``POST /agents/{agent_id}/voice/tts`` — ``{text, voice?}`` →
  ``{audio_base64, mime_type}``. On-demand "read this reply aloud".
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from jvspatial.api import endpoint
from jvspatial.api.exceptions import JVSpatialAPIException, ValidationError

from jvagent.action.interact.public_gate import (
    require_messenger_session,
    resolve_agent_action,
)

logger = logging.getLogger(__name__)

# Decoded-audio ceiling for an STT clip (mirrors the Deepgram provider cap).
_MAX_STT_BYTES = 25 * 1024 * 1024
# Character ceiling for a single TTS request.
_MAX_TTS_CHARS = 5000

_VOICE_DISABLED = "Voice is not enabled for this agent."


def _b64_decoded_len(b64: str) -> int:
    """Approximate decoded byte length of a base64 string without decoding it."""
    n = len(b64.strip())
    padding = b64.count("=", max(0, len(b64) - 2))
    return (n * 3) // 4 - padding


@endpoint(
    "/agents/{agent_id}/voice/stt",
    methods=["POST"],
    auth=False,
    tags=["Agent"],
)
async def voice_stt_endpoint(request: Request, agent_id: str) -> Any:
    """Transcribe a base64 audio clip using the agent's STT provider."""
    agent, _claims = await require_messenger_session(request, agent_id)

    try:
        body = await request.json()
    except Exception:
        raise ValidationError(message="Request body must be JSON.")
    if not isinstance(body, dict):
        raise ValidationError(message="Request body must be a JSON object.")

    audio_base64 = body.get("audio_base64")
    audio_type = str(body.get("audio_type") or "audio/webm")
    if not audio_base64 or not isinstance(audio_base64, str):
        raise ValidationError(
            message="audio_base64 is required.",
            details={"field": "audio_base64"},
        )
    if _b64_decoded_len(audio_base64) > _MAX_STT_BYTES:
        raise ValidationError(
            message="Audio clip exceeds the maximum allowed size.",
            details={"max_bytes": _MAX_STT_BYTES},
        )

    stt = await resolve_agent_action(agent, "BaseSTTAction")
    if stt is None:
        raise ValidationError(message=_VOICE_DISABLED, details={"capability": "stt"})

    try:
        transcript = await stt.invoke_base64(audio_base64, audio_type)
    except JVSpatialAPIException:
        raise
    except Exception as exc:
        logger.warning("voice/stt provider error: %s", exc)
        raise ValidationError(message="Transcription failed. Please try again.")

    return {"transcript": transcript or ""}


@endpoint(
    "/agents/{agent_id}/voice/tts",
    methods=["POST"],
    auth=False,
    tags=["Agent"],
)
async def voice_tts_endpoint(request: Request, agent_id: str) -> Any:
    """Synthesize speech (base64) from text using the agent's TTS provider."""
    agent, _claims = await require_messenger_session(request, agent_id)

    try:
        body = await request.json()
    except Exception:
        raise ValidationError(message="Request body must be JSON.")
    if not isinstance(body, dict):
        raise ValidationError(message="Request body must be a JSON object.")

    text = body.get("text")
    if not text or not isinstance(text, str) or not text.strip():
        raise ValidationError(message="text is required.", details={"field": "text"})
    if len(text) > _MAX_TTS_CHARS:
        raise ValidationError(
            message="Text exceeds the maximum length for synthesis.",
            details={"max_chars": _MAX_TTS_CHARS},
        )

    tts = await resolve_agent_action(agent, "BaseTTSAction")
    if tts is None:
        raise ValidationError(message=_VOICE_DISABLED, details={"capability": "tts"})

    try:
        audio_base64 = await tts.invoke(text, as_base64=True)
    except JVSpatialAPIException:
        raise
    except Exception as exc:
        logger.warning("voice/tts provider error: %s", exc)
        raise ValidationError(message="Speech synthesis failed. Please try again.")

    if not audio_base64:
        raise ValidationError(message="Speech synthesis produced no audio.")

    mime_type = getattr(tts, "output_mime_type", None) or "audio/mpeg"
    return {"audio_base64": audio_base64, "mime_type": mime_type}
