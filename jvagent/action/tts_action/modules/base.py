"""Base TTS module for text-to-speech implementations."""

import logging
from abc import ABC, abstractmethod
from typing import Dict, Optional, Union

logger = logging.getLogger(__name__)


class TTSModule(ABC):
    """Abstract base class for TTS implementations."""

    def __init__(self, api_key: Optional[str] = None, **kwargs):
        """Initialize TTS module.

        Args:
            api_key: API key for the TTS service
            **kwargs: Additional configuration parameters
        """
        self.api_key = api_key
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    async def invoke(
        self, text: str, as_base64: bool = False, as_url: bool = False
    ) -> Optional[Union[str, bytes]]:
        """Convert text to speech.

        Args:
            text: Text to convert to speech
            as_base64: Return audio as base64 encoded string (for inline use, e.g. web players)
            as_url: Return URL for downloading. Callers should pass True when output will be
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
