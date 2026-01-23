"""Deepgram STT implementation."""
import base64
import logging
import traceback
from typing import Dict, Optional, Union

import requests

from .base import STTModule

logger = logging.getLogger(__name__)


class DeepgramSTTModule(STTModule):
    """Deepgram speech-to-text implementation."""

    def __init__(self, api_key: Optional[str] = None, model: str = 'nova-2', smart_format: bool = True, **kwargs):
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

        transcript = None
        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "application/json"
        }
        data = {"url": audio_url}

        try:
            response = requests.post(
                "https://api.deepgram.com/v1/listen",
                headers=headers,
                json=data,
                params={
                    'model': self.model,
                    'smart_format': str(self.smart_format).lower()
                }
            )
            response.raise_for_status()

            if result := response.json():
                transcript = result.get('results', {}).get('channels', [{}])[0].get(
                    'alternatives', [{}]
                )[0].get('transcript', '')

        except requests.exceptions.HTTPError as e:
            self.logger.error(f"HTTP error occurred: {traceback.format_exc()}")
        except Exception as e:
            self.logger.error(f"An exception occurred: {traceback.format_exc()}")

        return transcript

    async def invoke_base64(self, audio_base64: str, audio_type: str = "audio/mp3") -> Optional[str]:
        """Convert audio from base64 to text using Deepgram API.
        
        Args:
            audio_base64: Base64 representation of the audio file
            audio_type: MIME type of the audio file
            
        Returns:
            Text transcript of audio or None if failed
        """
        if not self.api_key:
            return None

        transcript = None
        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": audio_type
        }
        data = base64.b64decode(audio_base64)

        try:
            response = requests.post(
                "https://api.deepgram.com/v1/listen",
                headers=headers,
                data=data,
                params={
                    'model': self.model,
                    'smart_format': str(self.smart_format).lower()
                }
            )
            response.raise_for_status()

            if result := response.json():
                transcript = result.get('results', {}).get('channels', [{}])[0].get(
                    'alternatives', [{}]
                )[0].get('transcript', '')

        except requests.exceptions.HTTPError as e:
            self.logger.error(f"HTTP error occurred: {traceback.format_exc()}")
        except Exception as e:
            self.logger.error(f"An exception occurred: {traceback.format_exc()}")

        return transcript

    async def invoke_file(self, audio_content: bytes, audio_type: str = "audio/mp3") -> Optional[Dict[str, Union[str, float]]]:
        """Convert audio file content to text using Deepgram API.
        
        Args:
            audio_content: Audio file content as bytes
            audio_type: MIME type of the audio file
            
        Returns:
            Dictionary with transcript and duration or None if failed
        """
        if not self.api_key:
            return None

        transcript = None
        duration = 0
        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": audio_type
        }

        try:
            response = requests.post(
                "https://api.deepgram.com/v1/listen",
                headers=headers,
                data=audio_content,
                params={
                    'model': self.model,
                    'smart_format': str(self.smart_format).lower()
                }
            )
            response.raise_for_status()

            if result := response.json():
                transcript = result.get('results', {}).get('channels', [{}])[0].get(
                    'alternatives', [{}]
                )[0].get('transcript', '')
                duration = result.get("metadata", {}).get("duration", 0)

        except requests.exceptions.HTTPError as e:
            self.logger.error(f"HTTP error occurred: {traceback.format_exc()}")
        except Exception as e:
            self.logger.error(f"An exception occurred: {traceback.format_exc()}")

        return {"transcript": transcript, "duration": duration} if transcript is not None else None

    async def healthcheck(self) -> Union[bool, Dict[str, str]]:
        """Perform health check for Deepgram API.
        
        Returns:
            True if healthy, error dict if unhealthy
        """
        if not self.api_key:
            return {
                "status": False,
                "message": "Deepgram API key is not set.",
                "severity": "error"
            }
        
        if not self.model:
            return {
                "status": False,
                "message": "Deepgram model is not set.",
                "severity": "error"
            }

        try:
            url = "https://api.deepgram.com/v1/projects"
            headers = {"Authorization": f"Token {self.api_key}"}
            response = requests.get(url, headers=headers)
            
            if response.status_code == 200:
                return True
            else:
                return {
                    "status": False,
                    "message": f"Check Deepgram STT configuration: {response.text}",
                    "severity": "error"
                }

        except Exception as e:
            self.logger.error(f"An exception occurred in healthcheck: {traceback.format_exc()}")
            return {
                "status": False,
                "message": f"Check Deepgram STT configuration: {e}",
                "severity": "error"
            }