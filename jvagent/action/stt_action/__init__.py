"""STT (Speech-to-Text) action package.

Provides BaseSTTAction and concrete provider implementations.
"""

from jvagent.action.stt_action.base import BaseSTTAction
from jvagent.action.stt_action.deepgram import DeepgramSTTAction

# Import endpoints to ensure they are discovered and registered
from . import endpoints  # noqa: F401

__all__ = ["BaseSTTAction", "DeepgramSTTAction"]
