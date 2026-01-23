"""STT Action Implementation."""
import logging
from typing import Dict, Optional, Union

from jvagent.action.base import Action
from jvspatial.core.annotations import attribute

from .modules.deepgram import DeepgramSTTModule

logger = logging.getLogger(__name__)


class STTAction(Action):
    """Action for Speech-to-Text integration using multiple providers."""

    provider: str = attribute(
        default="deepgram",
        description="STT provider (deepgram)",
    )

    api_key: Optional[str] = attribute(
        default=None, 
        description="STT API Key"
    )

    model: str = attribute(
        default="nova-2",
        description="STT model to use (enhanced, nova, base, nova-2)"
    )

    smart_format: bool = attribute(
        default=True,
        description="Enable smart formatting for transcripts"
    )

    def _get_stt_module(self):
        """Get the appropriate STT module based on provider."""
        if self.provider == "deepgram":
            return DeepgramSTTModule(
                api_key=self.api_key,
                model=self.model,
                smart_format=self.smart_format
            )
        else:
            raise ValueError(f"Unsupported STT provider: {self.provider}")

    async def invoke(self, audio_url: str) -> Optional[str]:
        """Convert speech to text from audio URL.

        Args:
            audio_url: URL of the audio file

        Returns:
            Text transcript of audio or None if failed
        """
        stt_module = self._get_stt_module()
        return await stt_module.invoke(audio_url)

    async def invoke_base64(self, audio_base64: str, audio_type: str = "audio/mp3") -> Optional[str]:
        """Convert an audio file from a base64 string to text.

        Args:
            audio_base64: Base64 representation of the audio file
            audio_type: MIME type of the audio file

        Returns:
            Transcription text or None if failed
        """
        stt_module = self._get_stt_module()
        return await stt_module.invoke_base64(audio_base64, audio_type)

    async def invoke_file(self, audio_content: bytes, audio_type: str = "audio/mp3") -> Optional[Dict[str, Union[str, float]]]:
        """Convert audio file content to text.

        Args:
            audio_content: Audio file content as bytes
            audio_type: MIME type of the audio file

        Returns:
            Dictionary with transcript and duration or None if failed
        """
        if self.provider == "deepgram":
            stt_module = self._get_stt_module()
            return await stt_module.invoke_file(audio_content, audio_type)
        else:
            # For other providers, fall back to base64 conversion
            import base64
            audio_base64 = base64.b64encode(audio_content).decode('utf-8')
            transcript = await self.invoke_base64(audio_base64, audio_type)
            return {"transcript": transcript, "duration": 0} if transcript else None

    async def healthcheck(self) -> Union[bool, Dict[str, str]]:
        """Perform health check for the STT service.

        Returns:
            True if healthy, error dict if unhealthy
        """
        if not self.api_key:
            return {
                "status": False,
                "message": "STT API key is not set.",
                "severity": "error"
            }

        try:
            stt_module = self._get_stt_module()
            return await stt_module.healthcheck()
        except Exception as e:
            logger.error(f"STT healthcheck failed: {e}")
            return {
                "status": False,
                "message": f"STT service error: {e}",
                "severity": "error"
            }