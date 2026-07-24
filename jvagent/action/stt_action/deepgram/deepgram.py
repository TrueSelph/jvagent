"""Deepgram speech-to-text action."""

import asyncio
import base64
import logging
from typing import AsyncIterator, Awaitable, Callable, Dict, Optional, Union

from deepgram import AsyncDeepgramClient
from deepgram.core.api_error import ApiError
from jvspatial.core.annotations import attribute
from jvspatial.env import env

from jvagent.action.stt_action.base import BaseSTTAction

logger = logging.getLogger(__name__)

# AUDIT-actions XC-17: cap STT audio uploads. 25 MB roughly accommodates
# a 30-minute mp3 at 128 kbps; bigger inputs are almost certainly
# accidental or hostile.
STT_MAX_BYTES = 25 * 1024 * 1024
ALLOWED_STT_MIME_TYPES = frozenset(
    {
        "audio/mp3",
        "audio/mpeg",
        "audio/wav",
        "audio/x-wav",
        "audio/ogg",
        "audio/webm",
        "audio/flac",
        "audio/m4a",
        "audio/x-m4a",
    }
)


class DeepgramSTTAction(BaseSTTAction):
    """Speech-to-text action using the Deepgram API."""

    model: str = attribute(
        default="nova-2",
        description="Model to use (enhanced, nova, base, nova-2)",
    )
    smart_format: bool = attribute(
        default=True,
        description="Enable smart formatting for transcripts",
    )

    @staticmethod
    def _env_api_key() -> str:
        return env("DEEPGRAM_API_KEY")

    def _get_client(self) -> AsyncDeepgramClient:
        """Lazy-initialize and return the async Deepgram client."""
        key = (self._env_api_key() or "").strip()
        client = getattr(self, "_deepgram_client", None)
        prev_key = getattr(self, "_deepgram_client_key", None)
        if client is not None and prev_key == key:
            return client
        self._deepgram_client = AsyncDeepgramClient(api_key=key)
        self._deepgram_client_key = key
        return self._deepgram_client

    def _extract_transcript(self, response) -> Optional[str]:
        """Extract transcript from SDK response."""
        if not response or not response.results:
            return None
        channels = response.results.channels or []
        if not channels:
            return None
        alternatives = channels[0].alternatives or []
        if not alternatives:
            return None
        return alternatives[0].transcript or ""

    def _get_duration(self, response) -> float:
        """Extract duration from SDK response metadata."""
        if not response or not response.metadata:
            return 0.0
        return getattr(response.metadata, "duration", 0.0) or 0.0

    async def invoke(self, audio_url: str) -> Optional[str]:
        """Convert audio from URL to text using Deepgram API."""
        if not (self._env_api_key() or "").strip():
            return None

        try:
            client = self._get_client()
            response = await client.listen.v1.media.transcribe_url(
                url=audio_url,
                model=self.model,
                smart_format=self.smart_format,
                request_options={"timeout_in_seconds": self.timeout},
            )
            return self._extract_transcript(response)
        except ApiError as e:
            body = str(e.body)[:500] if e.body else "(empty)"
            logger.error(
                "Deepgram API error: HTTP %d - %s",
                e.status_code,
                body,
            )
            return None
        except Exception as e:
            logger.error("Deepgram API error: %s", e, exc_info=True)
            return None

    async def invoke_base64(
        self, audio_base64: str, audio_type: str = "audio/mp3"
    ) -> Optional[str]:
        """Convert audio from base64 to text using Deepgram API.

        AUDIT-actions XC-17: reject payloads larger than ``STT_MAX_BYTES``
        (default 25 MB) and audio_type outside the allowed list. The
        base64 length-to-byte estimate is computed before the decode to
        avoid allocating huge buffers for hostile/accidental inputs.
        """
        if not (self._env_api_key() or "").strip():
            return None

        # MIME allowlist. Strip any codecs parameter first — browsers label
        # MediaRecorder output as e.g. "audio/webm;codecs=opus", which must
        # match the bare "audio/webm" entry (Deepgram sniffs the actual codec
        # from the bytes; audio_type is only used for this gate).
        mime_norm = (audio_type or "").split(";", 1)[0].strip().lower()
        if mime_norm not in ALLOWED_STT_MIME_TYPES:
            logger.warning(
                "invoke_base64: rejected audio_type=%r (allowed=%s)",
                audio_type,
                sorted(ALLOWED_STT_MIME_TYPES),
            )
            return None
        # Size cap pre-decode.
        raw_estimate = (len(audio_base64 or "") * 3) // 4
        if raw_estimate > STT_MAX_BYTES:
            logger.warning(
                "invoke_base64: payload ~%d bytes exceeds %d cap",
                raw_estimate,
                STT_MAX_BYTES,
            )
            return None
        data = base64.b64decode(audio_base64)
        try:
            client = self._get_client()
            response = await client.listen.v1.media.transcribe_file(
                request=data,
                model=self.model,
                smart_format=self.smart_format,
                request_options={"timeout_in_seconds": self.timeout},
            )
            return self._extract_transcript(response)
        except ApiError as e:
            body = str(e.body)[:500] if e.body else "(empty)"
            logger.error(
                "Deepgram API error: HTTP %d - %s",
                e.status_code,
                body,
            )
            return None
        except Exception as e:
            logger.error("Deepgram API error: %s", e, exc_info=True)
            return None

    async def invoke_file(
        self, audio_content: bytes, audio_type: str = "audio/mp3"
    ) -> Optional[Dict[str, Union[str, float]]]:
        """Convert audio file content to text using Deepgram API."""
        if not (self._env_api_key() or "").strip():
            return None

        try:
            client = self._get_client()
            response = await client.listen.v1.media.transcribe_file(
                request=audio_content,
                model=self.model,
                smart_format=self.smart_format,
                request_options={"timeout_in_seconds": self.timeout},
            )
            transcript = self._extract_transcript(response)
            if transcript is None:
                return None
            duration = self._get_duration(response)
            return {"transcript": transcript, "duration": duration}
        except ApiError as e:
            body = str(e.body)[:500] if e.body else "(empty)"
            logger.error(
                "Deepgram API error: HTTP %d - %s",
                e.status_code,
                body,
            )
            return None
        except Exception as e:
            logger.error("Deepgram API error: %s", e, exc_info=True)
            return None

    async def stream_transcribe(
        self,
        audio_iter: AsyncIterator[bytes],
        on_event: Callable[[dict], Awaitable[None]],
        *,
        language: Optional[str] = None,
    ) -> None:
        """Real-time transcription over Deepgram's live WebSocket.

        Pumps audio chunks from ``audio_iter`` (raw container bytes — a browser
        ``MediaRecorder`` webm/opus stream is forwarded verbatim; Deepgram sniffs
        the codec from the container, so no ``encoding`` is declared) into a live
        connection and invokes ``on_event`` for each transcript update:

        * ``{"type": "interim", "transcript": str}`` — partial (still changing)
        * ``{"type": "final", "transcript": str}`` — stable segment
        * ``{"type": "utterance_end"}`` — end-of-utterance marker
        * ``{"type": "error", "message": str}`` — provider/setup failure

        Returns when the audio iterator is exhausted and Deepgram has flushed its
        final results. Used by the messenger's streaming STT WebSocket endpoint.
        """
        if not (self._env_api_key() or "").strip():
            await on_event({"type": "error", "message": "stt_not_configured"})
            return

        connect_kwargs: Dict[str, Union[str, bool]] = {
            "model": self.model,
            "interim_results": True,
            "smart_format": self.smart_format,
        }
        if language:
            connect_kwargs["language"] = language

        try:
            client = self._get_client()
            async with client.listen.v1.connect(**connect_kwargs) as sock:

                async def _receiver() -> None:
                    async for msg in sock:
                        mtype = getattr(msg, "type", None)
                        if mtype == "Results":
                            try:
                                alt = msg.channel.alternatives[0]
                            except (AttributeError, IndexError):
                                continue
                            text = getattr(alt, "transcript", "") or ""
                            if not text:
                                continue
                            kind = (
                                "final"
                                if getattr(msg, "is_final", False)
                                else "interim"
                            )
                            await on_event({"type": kind, "transcript": text})
                        elif mtype == "UtteranceEnd":
                            await on_event({"type": "utterance_end"})

                recv_task = asyncio.create_task(_receiver())
                try:
                    async for chunk in audio_iter:
                        if chunk:
                            await sock.send_media(chunk)
                    await sock.send_close_stream()
                    # Bound the wait for Deepgram's final flush so a stuck socket
                    # can't hang the WebSocket handler indefinitely.
                    await asyncio.wait_for(recv_task, timeout=self.timeout)
                finally:
                    if not recv_task.done():
                        recv_task.cancel()
        except Exception as exc:  # provider/socket errors must not crash the WS
            logger.warning("Deepgram live stream error: %s", exc)
            await on_event({"type": "error", "message": "stt_stream_failed"})

    async def healthcheck(self) -> Union[bool, Dict[str, str]]:
        """Perform health check for Deepgram API."""
        if not (self._env_api_key() or "").strip():
            return {
                "status": False,
                "message": "DEEPGRAM_API_KEY is not set",
                "severity": "error",
            }

        if not self.model:
            return {
                "status": False,
                "message": "Deepgram model is not set",
                "severity": "error",
            }

        try:
            client = self._get_client()
            await client.manage.v1.projects.list(
                request_options={"timeout_in_seconds": self.timeout},
            )
            return True
        except ApiError as e:
            logger.error(
                "Deepgram healthcheck error: HTTP %d - %s", e.status_code, e.body
            )
            body = str(e.body) if e.body else str(e)
            return {
                "status": False,
                "message": f"Deepgram API error: {body}",
                "severity": "error",
            }
        except Exception as e:
            logger.error("Deepgram healthcheck error: %s", e, exc_info=True)
            return {
                "status": False,
                "message": f"Deepgram API error: {e}",
                "severity": "error",
            }
