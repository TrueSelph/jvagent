"""Deepgram speech-to-text action."""

import base64
import logging
from typing import Dict, Optional, Union

from deepgram import AsyncDeepgramClient
from deepgram.core.api_error import ApiError
from jvspatial.core.annotations import attribute
from jvspatial.env import env

from jvagent.action.stt_action.base import BaseSTTAction

logger = logging.getLogger(__name__)


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
        """Convert audio from base64 to text using Deepgram API."""
        if not (self._env_api_key() or "").strip():
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
