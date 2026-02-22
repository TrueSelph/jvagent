"""STT (Speech-to-Text) action package.

This module provides speech-to-text integration using various providers.
"""

# Import endpoints module to ensure endpoints are discovered and registered
from . import endpoints  # noqa: F401
from .stt_action import STTAction

__all__ = ["STTAction"]
