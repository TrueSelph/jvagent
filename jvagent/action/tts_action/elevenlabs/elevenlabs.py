"""ElevenLabs text-to-speech action."""

import base64
import logging
import uuid
from typing import Dict, List, Optional, Union

from jvspatial.core.annotations import attribute
from jvspatial.env import env

from jvagent.action.tts_action.base import BaseTTSAction

logger = logging.getLogger(__name__)


class ElevenLabsTTSAction(BaseTTSAction):
    """Text-to-speech action using the ElevenLabs API."""

    model: str = attribute(default="eleven_turbo_v2", description="Model to use")
    voice: str = attribute(default="Sarah", description="Voice to use for synthesis")

    @staticmethod
    def _env_api_key() -> str:
        return env("ELEVENLABS_API_KEY")

    async def invoke(
        self, text: str, as_base64: bool = False, as_url: bool = False
    ) -> Optional[Union[str, bytes]]:
        """Convert text to speech using ElevenLabs API."""
        api_key = (self._env_api_key() or "").strip()
        if not api_key:
            return None

        audio = None
        try:
            # The ElevenLabs SDK is synchronous; run the blocking calls in a
            # worker thread so the event loop is not stalled.
            # AUDIT-actions XC-3.
            import asyncio

            from elevenlabs.client import ElevenLabs

            client = ElevenLabs(api_key=api_key)
            voice_id = await self._get_voice_id(self.voice)

            def _convert() -> bytes:
                response = client.text_to_speech.convert(
                    voice_id=voice_id,
                    text=text,
                    model_id=self.model,
                )
                return b"".join(response) if response else b""

            audio = await asyncio.to_thread(_convert)

        except Exception as e:
            logger.error("ElevenLabs API error: %s", e, exc_info=True)
            return None

        return await self.get_audio_as(audio, as_base64, as_url)

    async def _get_voice_id(self, name: str) -> str:
        """Resolve voice name to voice_id."""
        result = await self.get_voice_by_name(name)
        if isinstance(result, str):
            return result
        if isinstance(result, dict) and "voice_id" in result:
            return result["voice_id"]
        raise ValueError(f"Voice '{name}' not found")

    async def get_audio_as(
        self, audio: bytes, as_base64: bool = False, as_url: bool = False
    ) -> Optional[Union[str, bytes]]:
        """Prepare audio bytes as base64 string or URL for download."""
        if not audio:
            return None

        if as_base64:
            return base64.b64encode(audio).decode("utf-8")

        if as_url:
            filename = f"tts_audio_{uuid.uuid4().hex}.mp3"
            storage_path = f"tts_audio/{filename}"

            try:
                success = await self.save_file(
                    storage_path, audio, metadata={"type": "tts_audio"}
                )
                if success:
                    url = await self.get_file_url(storage_path)
                    return url
            except Exception as e:
                logger.error("Error saving audio file: %s", e, exc_info=True)
                return None

        return audio

    async def get_voices(self) -> List[Dict[str, str]]:
        """Get all available voices."""
        api_key = (self._env_api_key() or "").strip()
        if not api_key:
            return []

        try:
            import asyncio

            from elevenlabs.client import ElevenLabs

            client = ElevenLabs(api_key=api_key)
            # SDK call is synchronous; offload to a thread. AUDIT-actions XC-3.
            result = await asyncio.to_thread(client.voices.get_all)

            if result:
                return [
                    {
                        "name": v.name,
                        "voice_id": v.voice_id,
                        "category": v.category,
                    }
                    for v in result.voices
                ]
        except Exception as e:
            logger.error("Error getting voices: %s", e, exc_info=True)

        return []

    async def get_voice_by_name(
        self, name: str
    ) -> Optional[Union[str, Dict[str, str]]]:
        """Get voice_id by name. Returns voice_id string for API use."""
        api_key = (self._env_api_key() or "").strip()
        if not api_key:
            return None

        try:
            import asyncio

            from elevenlabs.client import ElevenLabs

            client = ElevenLabs(api_key=api_key)
            # SDK call is synchronous; offload to a thread. AUDIT-actions XC-3.
            voices = (await asyncio.to_thread(client.voices.get_all)).voices

            name_lower = name.strip().lower()

            for voice in voices:
                if voice.name.lower() == name_lower:
                    return voice.voice_id

            for voice in voices:
                if name_lower in voice.name.lower():
                    return voice.voice_id

            available = ", ".join([v.name for v in voices[:15]])
            raise ValueError(f"Voice '{name}' not found. Available: {available}")

        except Exception as e:
            logger.error("Error getting voice by name: %s", e, exc_info=True)

        return None

    async def get_models(self) -> List[Dict[str, str]]:
        """Get all available models."""
        api_key = (self._env_api_key() or "").strip()
        if not api_key:
            return []

        try:
            import asyncio

            from elevenlabs.client import ElevenLabs

            client = ElevenLabs(api_key=api_key)
            # SDK call is synchronous; offload to a thread. AUDIT-actions XC-3.
            result = await asyncio.to_thread(client.models.get_all)

            if result:
                return [
                    {
                        "name": m.name,
                        "model_id": m.model_id,
                        "description": m.description,
                    }
                    for m in result
                ]
        except Exception as e:
            logger.error("Error getting models: %s", e, exc_info=True)

        return []

    async def healthcheck(self) -> Union[bool, Dict[str, str]]:
        """Perform health check for ElevenLabs API."""
        if not (self._env_api_key() or "").strip():
            return {
                "status": False,
                "message": "ELEVENLABS_API_KEY is not set.",
                "severity": "error",
            }

        try:
            models = await self.get_models()
            if models:
                return True
            return {
                "status": False,
                "message": "ElevenLabs API key may be incorrect or subscription expired.",
                "severity": "error",
            }
        except Exception as e:
            logger.error("ElevenLabs healthcheck error: %s", e, exc_info=True)
            return {
                "status": False,
                "message": f"Check ElevenLabs configuration: {e}",
                "severity": "error",
            }
