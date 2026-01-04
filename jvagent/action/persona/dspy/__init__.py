"""DSPy integration for persona response generation.

This module provides DSPy signatures and modules for generating persona responses
with optimization capabilities via DSPy teleprompters.
"""

from jvagent.action.persona.dspy.modules import PersonaResponseModule
from jvagent.action.persona.dspy.signatures import PersonaResponse

__all__ = ["PersonaResponseModule", "PersonaResponse"]

