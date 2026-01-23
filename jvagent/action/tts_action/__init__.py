"""TTS (Text-to-Speech) action package.

This module provides text-to-speech integration using various providers.
"""

from .tts_action import TTSAction

# Import endpoints module to ensure endpoints are discovered and registered
from . import endpoints  # noqa: F401

__all__ = ["TTSAction"]