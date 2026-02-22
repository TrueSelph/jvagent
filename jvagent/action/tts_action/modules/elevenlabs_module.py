"""ElevenLabs TTS implementation."""

import base64
import logging
import traceback
from typing import Dict, List, Optional, Union

# from elevenlabs import ElevenLabs
from elevenlabs.client import ElevenLabs

from .base import TTSModule

logger = logging.getLogger(__name__)


class ElevenLabsTTSModule(TTSModule):
    """ElevenLabs text-to-speech implementation."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "eleven_turbo_v2",
        voice: str = "Sarah",
        action=None,
        **kwargs,
    ):
        """Initialize ElevenLabs TTS module.

        Args:
            api_key: ElevenLabs API key
            model: Model to use
            voice: Voice to use
            action: Parent action instance for file storage
            **kwargs: Additional configuration parameters
        """
        super().__init__(api_key, **kwargs)
        self.model = model
        self.voice = voice
        self.action = action

    async def invoke(
        self, text: str, as_base64: bool = False, as_url: bool = True
    ) -> Optional[Union[str, bytes]]:
        """Convert text to speech using ElevenLabs API.

        Args:
            text: Text to convert to speech
            as_base64: Return audio as base64 encoded string
            as_url: Return URL for downloading audio file

        Returns:
            Audio data as bytes, base64 string, URL, or None if failed
        """
        if not self.api_key:
            return None

        audio = None
        try:
            client = ElevenLabs(api_key=self.api_key)
            response = client.text_to_speech.convert(
                voice_id=await self.get_voice_by_name(self.voice),
                text=text,
                model_id=self.model,
            )

            if response:
                # Unify the byte chunks of audio data that comes back
                audio = b"".join(response)

        except Exception as e:
            self.logger.error(f"An exception occurred: {traceback.format_exc()}")
            return None

        return self.get_audio_as(audio, as_base64, as_url)

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

        if as_base64:
            return base64.b64encode(audio).decode("utf-8")

        if as_url and self.action:
            import uuid

            filename = f"tts_audio_{uuid.uuid4().hex}.mp3"
            storage_path = f"tts_audio/{filename}"

            try:
                # Use action's save_file method which uses jvspatial storage
                import asyncio

                if asyncio.iscoroutinefunction(self.action.save_file):
                    # Run async save_file
                    loop = asyncio.get_event_loop()
                    success = loop.run_until_complete(
                        self.action.save_file(
                            storage_path, audio, metadata={"type": "tts_audio"}
                        )
                    )
                else:
                    success = self.action.save_file(
                        storage_path, audio, metadata={"type": "tts_audio"}
                    )

                if success:
                    # Get URL using action's get_file_url method
                    if asyncio.iscoroutinefunction(self.action.get_file_url):
                        url = loop.run_until_complete(
                            self.action.get_file_url(storage_path)
                        )
                    else:
                        url = self.action.get_file_url(storage_path)
                    return url

            except Exception as e:
                self.logger.error(f"Error saving audio file: {e}")
                return None

        # Return raw bytes
        return audio

    async def get_voices(self) -> List[Dict[str, str]]:
        """Get all available voices.

        Returns:
            List of voice information dictionaries
        """
        if not self.api_key:
            return []

        try:
            client = ElevenLabs(api_key=self.api_key)
            result = client.voices.get_all()

            if result:
                voices = []
                for voice in result.voices:
                    voices.append(
                        {
                            "name": voice.name,
                            "voice_id": voice.voice_id,
                            "category": voice.category,
                        }
                    )
                return voices
        except Exception as e:
            self.logger.error(f"Error getting voices: {e}")

        return []

    async def get_voice_by_name(self, name: str) -> Optional[Dict[str, str]]:
        """Get voice information by name.

        Args:
            name: Name of the voice

        Returns:
            Voice information dictionary or None if not found
        """
        if not self.api_key:
            return None

        try:
            client = ElevenLabs(api_key=self.api_key)
            voices = client.voices.get_all().voices

            name_lower = name.strip().lower()

            for voice in voices:
                if voice.name.lower() == name_lower:
                    return voice.voice_id

            # If exact match fails, try partial match (useful for cloned voices)
            for voice in voices:
                if name_lower in voice.name.lower():
                    return voice.voice_id

            # List available names for better error message
            available = ", ".join([v.name for v in voices[:15]])  # limit spam
            raise ValueError(f"Voice '{name}' not found. Available voices: {available}")

        except Exception as e:
            self.logger.error(f"Error getting voice by name: {e}")

        return None

    async def get_models(self) -> List[Dict[str, str]]:
        """Get all available models.

        Returns:
            List of model information dictionaries
        """
        if not self.api_key:
            return []

        try:
            client = ElevenLabs(api_key=self.api_key)
            result = client.models.get_all()

            if result:
                models = []
                for model in result:
                    models.append(
                        {
                            "name": model.name,
                            "model_id": model.model_id,
                            "description": model.description,
                        }
                    )
                return models
        except Exception as e:
            self.logger.error(f"Error getting models: {e}")

        return []

    async def healthcheck(self) -> Union[bool, Dict[str, str]]:
        """Perform health check for ElevenLabs API.

        Returns:
            True if healthy, error dict if unhealthy
        """
        if not self.api_key:
            return {
                "status": False,
                "message": "ElevenLabs API key is not set.",
                "severity": "error",
            }

        try:
            models = await self.get_models()
            if models:
                return True
            else:
                return {
                    "status": False,
                    "message": "ElevenLabs TTS Action API key may be incorrect or your subscription may be expired.",
                    "severity": "error",
                }
        except Exception as e:
            self.logger.error(
                f"An exception occurred in healthcheck: {traceback.format_exc()}"
            )
            return {
                "status": False,
                "message": f"Check ElevenLabs TTS Action configuration: {e}",
                "severity": "error",
            }
