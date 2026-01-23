"""TTS Action Implementation."""
import logging
from typing import Dict, List, Optional, Union

from jvagent.action.base import Action
from jvspatial.core.annotations import attribute

from .modules.elevenlabs_module import ElevenLabsTTSModule

logger = logging.getLogger(__name__)


class TTSAction(Action):
    """Action for Text-to-Speech integration using multiple providers."""

    provider: str = attribute(
        default="elevenlabs",
        description="TTS provider (elevenlabs)",
    )

    api_key: Optional[str] = attribute(
        default=None, 
        description="TTS API Key"
    )

    model: str = attribute(
        default="eleven_turbo_v2",
        description="TTS model to use"
    )

    voice: str = attribute(
        default="Sarah",
        description="Voice to use for speech synthesis"
    )

    def _get_tts_module(self):
        """Get the appropriate TTS module based on provider."""
        if self.provider == "elevenlabs":
            return ElevenLabsTTSModule(
                api_key=self.api_key,
                model=self.model,
                voice=self.voice,
                action=self  
            )
        else:
            raise ValueError(f"Unsupported TTS provider: {self.provider}")

    async def invoke(self, text: str, as_base64: bool = False, as_url: bool = False) -> Optional[Union[str, bytes]]:
        """Convert text to speech and save the audio file.

        Args:
            text: Text to convert to speech
            as_base64: Return audio as base64 encoded string
            as_url: Return URL for downloading audio file

        Returns:
            Audio data as bytes, base64 string, URL, or None if failed
        """
        tts_module = self._get_tts_module()
        return await tts_module.invoke(text, as_base64, as_url)

    def get_audio_as(self, audio: bytes, as_base64: bool = False, as_url: bool = False) -> Optional[Union[str, bytes]]:
        """Prepare audio bytes as base64 string or URL for download.

        Args:
            audio: Audio data as bytes
            as_base64: Return as base64 encoded string
            as_url: Return URL for downloading

        Returns:
            Audio data as bytes, base64 string, URL, or None if failed
        """
        tts_module = self._get_tts_module()
        return tts_module.get_audio_as(audio, as_base64, as_url)

    async def get_voices(self) -> List[Dict[str, str]]:
        """Get all available voices for the current provider.

        Returns:
            List of voice information dictionaries
        """
        if self.provider == "elevenlabs":
            tts_module = self._get_tts_module()
            return await tts_module.get_voices()
        else:
            return []
    
    async def get_voice_by_name(self, name: str) -> Optional[Dict[str, str]]:
        """Get voice information by name for the current provider.

        Args:
            name: Name of the voice

        Returns:
            Voice information dictionary or None if not found
        """
        if self.provider == "elevenlabs":
            tts_module = self._get_tts_module()
            return await tts_module.get_voice_by_name(name)
        else:
            return None

    async def get_models(self) -> List[Dict[str, str]]:
        """Get all available models for the current provider.

        Returns:
            List of model information dictionaries
        """
        if self.provider == "elevenlabs":
            tts_module = self._get_tts_module()
            return await tts_module.get_models()
        else:
            return []

    async def healthcheck(self) -> Union[bool, Dict[str, str]]:
        """Perform health check for the TTS service.

        Returns:
            True if healthy, error dict if unhealthy
        """
        if not self.api_key:
            return {
                "status": False,
                "message": "TTS API key is not set.",
                "severity": "error"
            }

        try:
            tts_module = self._get_tts_module()
            return await tts_module.healthcheck()
        except Exception as e:
            logger.error(f"TTS healthcheck failed: {e}")
            return {
                "status": False,
                "message": f"TTS service error: {e}",
                "severity": "error"
            }