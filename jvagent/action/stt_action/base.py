"""Base class for speech-to-text actions.

All STT action implementations must inherit from BaseSTTAction and implement
the abstract methods. Concrete providers (e.g. DeepgramSTTAction) extend this
class and implement the transcription API calls.
"""

import base64
import logging
from abc import ABC, abstractmethod
from typing import Dict, Optional, Union

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action

logger = logging.getLogger(__name__)


class BaseSTTAction(Action, ABC):
    """Abstract base class for speech-to-text actions.

    Concrete implementations (e.g. DeepgramSTTAction) extend this class and
    implement invoke, invoke_base64, invoke_file, and healthcheck.

    Usage (agent.yaml):
        Register a concrete provider action (e.g. jvagent/deepgram_stt).
        Point WhatsAppAction.stt_action at its class name (e.g. DeepgramSTTAction).

        Provider API keys are read from the environment (``.env``), not stored on the action.
    """

    timeout: int = attribute(default=30, description="Request timeout in seconds", ge=1)

    @abstractmethod
    async def invoke(self, audio_url: str) -> Optional[str]:
        """Convert speech from URL to text.

        Args:
            audio_url: URL of the audio file

        Returns:
            Text transcript of audio or None if failed
        """

    @abstractmethod
    async def invoke_base64(
        self, audio_base64: str, audio_type: str = "audio/mp3"
    ) -> Optional[str]:
        """Convert speech from base64 to text.

        Args:
            audio_base64: Base64 representation of the audio file
            audio_type: MIME type of the audio (e.g. audio/ogg, audio/mp3).
                Callers should pass this when known; different sources use different
                formats (e.g. WhatsApp voice = audio/ogg). Default is for legacy use.

        Returns:
            Text transcript of audio or None if failed
        """

    async def invoke_file(
        self, audio_content: bytes, audio_type: str = "audio/mp3"
    ) -> Optional[Dict[str, Union[str, float]]]:
        """Convert audio file content to text.

        Default implementation: base64-encode and call invoke_base64.
        Override for providers with native file support (e.g. duration).

        Args:
            audio_content: Audio file content as bytes
            audio_type: MIME type of the audio file

        Returns:
            Dictionary with transcript and optional duration, or None if failed
        """
        audio_base64 = base64.b64encode(audio_content).decode("utf-8")
        transcript = await self.invoke_base64(audio_base64, audio_type)
        return {"transcript": transcript, "duration": 0} if transcript else None

    @abstractmethod
    async def healthcheck(self) -> Union[bool, Dict[str, str]]:
        """Perform health check for the STT service.

        Returns:
            True if healthy, False or error dict if unhealthy
        """
