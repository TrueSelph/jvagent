"""Base class for text-to-speech actions.

All TTS action implementations must inherit from BaseTTSAction and implement
the abstract methods. Concrete providers (e.g. ElevenLabsTTSAction) extend
this class and implement the synthesis API calls.
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Union

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action

logger = logging.getLogger(__name__)


class BaseTTSAction(Action, ABC):
    """Abstract base class for text-to-speech actions.

    Concrete implementations (e.g. ElevenLabsTTSAction) extend this class and
    implement invoke, get_audio_as, and healthcheck. Optional methods
    get_voices, get_voice_by_name, get_models return empty/None if not supported.

    Usage (agent.yaml):
        Register a concrete provider action (e.g. jvagent/elevenlabs_tts).
        Point WhatsAppAction.tts_action at its class name (e.g. ElevenLabsTTSAction).

        Provider API keys are read from the environment (``.env``), not stored on the action.
    """

    @abstractmethod
    async def invoke(
        self, text: str, as_base64: bool = False, as_url: bool = False
    ) -> Optional[Union[str, bytes]]:
        """Convert text to speech.

        Args:
            text: Text to convert to speech
            as_base64: Return audio as base64 (for inline use, e.g. web players)
            as_url: Return URL for downloading. Use True when output will be
                sent to adapters (e.g. WhatsApp) that need a URL.

        Returns:
            Audio data as bytes, base64 string, URL, or None if failed
        """
        pass

    @abstractmethod
    async def get_audio_as(
        self, audio: bytes, as_base64: bool = False, as_url: bool = False
    ) -> Optional[Union[str, bytes]]:
        """Prepare audio bytes as base64 string or URL for download.

        Args:
            audio: Audio data as bytes
            as_base64: Return as base64 encoded string
            as_url: Return URL for downloading (requires storage; use for adapter delivery)

        Returns:
            Audio data as bytes, base64 string, URL, or None if failed
        """
        pass

    @abstractmethod
    async def healthcheck(self) -> Union[bool, Dict[str, str]]:
        """Perform health check for the TTS service.

        Returns:
            True if healthy, False or error dict if unhealthy
        """
        pass

    async def get_voices(self) -> List[Dict[str, str]]:
        """Get all available voices. Override if provider supports it."""
        return []

    async def get_voice_by_name(self, name: str) -> Optional[Dict[str, str]]:
        """Get voice information by name. Override if provider supports it."""
        return None

    async def get_models(self) -> List[Dict[str, str]]:
        """Get all available models. Override if provider supports it."""
        return []
