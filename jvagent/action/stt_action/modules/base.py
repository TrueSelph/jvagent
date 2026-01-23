"""Base STT module for speech-to-text implementations."""
import logging
from abc import ABC, abstractmethod
from typing import Dict, Optional, Union

logger = logging.getLogger(__name__)


class STTModule(ABC):
    """Abstract base class for STT implementations."""

    def __init__(self, api_key: Optional[str] = None, **kwargs):
        """Initialize STT module.
        
        Args:
            api_key: API key for the STT service
            **kwargs: Additional configuration parameters
        """
        self.api_key = api_key
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    async def invoke(self, audio_url: str) -> Optional[str]:
        """Convert speech from URL to text.
        
        Args:
            audio_url: URL of the audio file
            
        Returns:
            Text transcript of audio or None if failed
        """
        pass

    @abstractmethod
    async def invoke_base64(self, audio_base64: str, audio_type: str = "audio/mp3") -> Optional[str]:
        """Convert speech from base64 to text.
        
        Args:
            audio_base64: Base64 representation of the audio file
            audio_type: MIME type of the audio file
            
        Returns:
            Text transcript of audio or None if failed
        """
        pass

    @abstractmethod
    async def healthcheck(self) -> Union[bool, Dict[str, str]]:
        """Perform health check for the STT service.
        
        Returns:
            True if healthy, False or error dict if unhealthy
        """
        pass