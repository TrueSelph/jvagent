"""STT modules package."""

from .base import STTModule
from .deepgram import DeepgramSTTModule

__all__ = ["STTModule", "DeepgramSTTModule"]