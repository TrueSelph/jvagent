"""TTS Action Implementation."""

import logging
from typing import Dict, List, Optional, Union

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action

from .modules.elevenlabs_module import ElevenLabsTTSModule

logger = logging.getLogger(__name__)


class TTSAction(Action):
    """Text-to-Speech action for converting text to speech using multiple providers."""

    provider: str = attribute(
        default="elevenlabs", description="TTS provider (elevenlabs)"
    )

    api_key: Optional[str] = attribute(default=None, description="TTS API Key")

    model: str = attribute(default="eleven_turbo_v2", description="TTS model to use")

    voice: str = attribute(
        default="Sarah", description="Voice to use for speech synthesis"
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._tts_module = None

    async def on_register(self) -> None:
        """Called when action is registered."""
        logger.info(f"TTSAction registered with provider: {self.provider}")

    async def on_enable(self) -> None:
        """Called when action is enabled."""
        if not self.api_key:
            logger.warning("TTS API key not configured")
        logger.info(
            f"TTSAction enabled (provider: {self.provider}, model: {self.model}, voice: {self.voice})"
        )
        # Initialize TTS module for caching
        self._tts_module = None

    def _get_tts_module(self):
        """Get the appropriate TTS module based on provider (cached)."""
        if self._tts_module is None:
            if self.provider == "elevenlabs":
                self._tts_module = ElevenLabsTTSModule(
                    api_key=self.api_key,
                    model=self.model,
                    voice=self.voice,
                    action=self,
                )
            else:
                raise ValueError(f"Unsupported TTS provider: {self.provider}")
        return self._tts_module

    async def invoke(
        self, text: str, as_base64: bool = False, as_url: bool = False
    ) -> Optional[Union[str, bytes]]:
        """Convert text to speech and save the audio file.

        Args:
            text: Text to convert to speech
            as_base64: Return audio as base64 encoded string
            as_url: Return URL for downloading audio file

        Returns:
            Audio data as bytes, base64 string, URL, or None if failed
        """
        if not text or not isinstance(text, str) or not text.strip():
            logger.warning("Invalid text input for TTS synthesis")
            return None

        try:
            tts_module = self._get_tts_module()
            return await tts_module.invoke(text, as_base64, as_url)
        except Exception as e:
            logger.error(f"TTS invoke failed: {e}", exc_info=True)
            return None

    def get_audio_as(
        self, audio: bytes, as_base64: bool = False, as_url: bool = False
    ) -> Optional[Union[str, bytes]]:
        """Prepare audio bytes as base64 string or URL for download.

        Args:
            audio: Audio data as bytes
            as_base64: Return as base64 encoded string
            as_url: Return URL for downloading

        Returns:
            Audio data as bytes, base64 string, URL, or None if failed
        """
        if not audio:
            return None

        try:
            tts_module = self._get_tts_module()
            return tts_module.get_audio_as(audio, as_base64, as_url)
        except Exception as e:
            logger.error(f"TTS get_audio_as failed: {e}", exc_info=True)
            return None

    async def get_voices(self) -> List[Dict[str, str]]:
        """Get all available voices for the current provider.

        Returns:
            List of voice information dictionaries
        """
        try:
            if self.provider == "elevenlabs":
                tts_module = self._get_tts_module()
                return await tts_module.get_voices()
            else:
                return []
        except Exception as e:
            logger.error(f"TTS get_voices failed: {e}", exc_info=True)
            return []

    async def get_voice_by_name(self, name: str) -> Optional[Dict[str, str]]:
        """Get voice information by name for the current provider.

        Args:
            name: Name of the voice

        Returns:
            Voice information dictionary or None if not found
        """
        try:
            if self.provider == "elevenlabs":
                tts_module = self._get_tts_module()
                return await tts_module.get_voice_by_name(name)
            else:
                return None
        except Exception as e:
            logger.error(f"TTS get_voice_by_name failed: {e}", exc_info=True)
            return None

    async def get_models(self) -> List[Dict[str, str]]:
        """Get all available models for the current provider.

        Returns:
            List of model information dictionaries
        """
        try:
            if self.provider == "elevenlabs":
                tts_module = self._get_tts_module()
                return await tts_module.get_models()
            else:
                return []
        except Exception as e:
            logger.error(f"TTS get_models failed: {e}", exc_info=True)
            return []

    async def healthcheck(self) -> Union[bool, Dict[str, str]]:
        """Perform health check for the TTS service.

        Returns:
            True if healthy, error dict if unhealthy
        """
        if not self.api_key:
            return {
                "status": False,
                "message": "TTS API key is not set",
                "severity": "error",
            }

        try:
            tts_module = self._get_tts_module()
            return await tts_module.healthcheck()
        except Exception as e:
            logger.error(f"TTS healthcheck failed: {e}", exc_info=True)
            return {
                "status": False,
                "message": f"TTS service error: {e}",
                "severity": "error",
            }
