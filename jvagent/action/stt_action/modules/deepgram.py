"""Deepgram STT implementation."""

import base64
import json
import logging
from typing import Dict, Optional, Union

import aiohttp

from .base import STTModule

logger = logging.getLogger(__name__)


class DeepgramSTTModule(STTModule):
    """Deepgram speech-to-text implementation."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "nova-2",
        smart_format: bool = True,
        **kwargs,
    ):
        """Initialize Deepgram STT module.

        Args:
            api_key: Deepgram API key
            model: Model to use (enhanced, nova, base, nova-2)
            smart_format: Enable smart formatting
            **kwargs: Additional configuration parameters
        """
        super().__init__(api_key, **kwargs)
        self.model = model
        self.smart_format = smart_format

    async def invoke(self, audio_url: str) -> Optional[str]:
        """Convert audio from URL to text using Deepgram API.

        Args:
            audio_url: URL of the audio file

        Returns:
            Text transcript of audio or None if failed
        """
        if not self.api_key:
            return None

        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "application/json",
        }
        data = {"url": audio_url}
        params = {"model": self.model, "smart_format": str(self.smart_format).lower()}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.deepgram.com/v1/listen",
                    headers=headers,
                    json=data,
                    params=params,
                ) as response:
                    body = await response.text()
                    if response.status >= 400:
                        logger.error(
                            "Deepgram API error: HTTP %d - %s",
                            response.status,
                            body[:500] if body else "(empty)",
                        )
                        return None
                    result = json.loads(body) if body else {}
                    if result:
                        channels = result.get("results", {}).get("channels", [])
                        if channels:
                            alternatives = channels[0].get("alternatives", [])
                            if alternatives:
                                return alternatives[0].get("transcript", "")
        except Exception as e:
            logger.error("Deepgram API error: %s", e, exc_info=True)

        return None

    async def invoke_base64(
        self, audio_base64: str, audio_type: str = "audio/mp3"
    ) -> Optional[str]:
        """Convert audio from base64 to text using Deepgram API.

        Args:
            audio_base64: Base64 representation of the audio file
            audio_type: MIME type of the audio file

        Returns:
            Text transcript of audio or None if failed
        """
        if not self.api_key:
            return None

        headers = {"Authorization": f"Token {self.api_key}", "Content-Type": audio_type}
        data = base64.b64decode(audio_base64)
        params = {"model": self.model, "smart_format": str(self.smart_format).lower()}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.deepgram.com/v1/listen",
                    headers=headers,
                    data=data,
                    params=params,
                ) as response:
                    body = await response.text()
                    if response.status >= 400:
                        logger.error(
                            "Deepgram API error: HTTP %d - %s",
                            response.status,
                            body[:500] if body else "(empty)",
                        )
                        return None
                    result = json.loads(body) if body else {}
                    if result:
                        channels = result.get("results", {}).get("channels", [])
                        if channels:
                            alternatives = channels[0].get("alternatives", [])
                            if alternatives:
                                return alternatives[0].get("transcript", "")
        except Exception as e:
            logger.error("Deepgram API error: %s", e, exc_info=True)

        return None

    async def invoke_file(
        self, audio_content: bytes, audio_type: str = "audio/mp3"
    ) -> Optional[Dict[str, Union[str, float]]]:
        """Convert audio file content to text using Deepgram API.

        Args:
            audio_content: Audio file content as bytes
            audio_type: MIME type of the audio file

        Returns:
            Dictionary with transcript and duration or None if failed
        """
        if not self.api_key:
            return None

        headers = {"Authorization": f"Token {self.api_key}", "Content-Type": audio_type}
        params = {"model": self.model, "smart_format": str(self.smart_format).lower()}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.deepgram.com/v1/listen",
                    headers=headers,
                    data=audio_content,
                    params=params,
                ) as response:
                    body = await response.text()
                    if response.status >= 400:
                        logger.error(
                            "Deepgram API error: HTTP %d - %s",
                            response.status,
                            body[:500] if body else "(empty)",
                        )
                        return None
                    result = json.loads(body) if body else {}
                    if result:
                        channels = result.get("results", {}).get("channels", [])
                        if channels:
                            alternatives = channels[0].get("alternatives", [])
                            if alternatives:
                                transcript = alternatives[0].get("transcript", "")
                                duration = result.get("metadata", {}).get("duration", 0)
                                return {"transcript": transcript, "duration": duration}
        except Exception as e:
            logger.error("Deepgram API error: %s", e, exc_info=True)

        return None

    async def healthcheck(self) -> Union[bool, Dict[str, str]]:
        """Perform health check for Deepgram API.

        Returns:
            True if healthy, error dict if unhealthy
        """
        if not self.api_key:
            return {
                "status": False,
                "message": "Deepgram API key is not set",
                "severity": "error",
            }

        if not self.model:
            return {
                "status": False,
                "message": "Deepgram model is not set",
                "severity": "error",
            }

        try:
            headers = {"Authorization": f"Token {self.api_key}"}

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://api.deepgram.com/v1/projects", headers=headers
                ) as response:
                    if response.status == 200:
                        return True
                    else:
                        text = await response.text()
                        return {
                            "status": False,
                            "message": f"Deepgram API error: {text}",
                            "severity": "error",
                        }

        except Exception as e:
            logger.error("Deepgram healthcheck error: %s", e, exc_info=True)
            return {
                "status": False,
                "message": f"Deepgram API error: {e}",
                "severity": "error",
            }
