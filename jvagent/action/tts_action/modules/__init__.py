"""TTS modules package."""

from .base import TTSModule
from .elevenlabs_module import ElevenLabsTTSModule

__all__ = ["TTSModule", "ElevenLabsTTSModule"]