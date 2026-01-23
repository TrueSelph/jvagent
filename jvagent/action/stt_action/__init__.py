"""STT (Speech-to-Text) action package.

This module provides speech-to-text integration using various providers.
"""

from .stt_action import STTAction

# Import endpoints module to ensure endpoints are discovered and registered
from . import endpoints  # noqa: F401

__all__ = ["STTAction"]