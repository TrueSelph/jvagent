"""TTS (Text-to-Speech) action package.

Provides BaseTTSAction and concrete provider implementations.
"""

from jvagent.action.tts_action.base import BaseTTSAction
from jvagent.action.tts_action.elevenlabs import ElevenLabsTTSAction

# Import endpoints to ensure they are discovered and registered
from . import endpoints  # noqa: F401

__all__ = ["BaseTTSAction", "ElevenLabsTTSAction"]
