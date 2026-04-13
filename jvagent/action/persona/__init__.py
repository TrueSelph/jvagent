"""PersonaAction module for agent behavioral modeling.

This module provides the PersonaAction, a simplified tool-based action for applying
agent prompts with configurable parameters.

Key Components:
- PersonaAction: Simplified tool-based action with respond() method
- Prompts: Prompt composition utilities

Note: PersonaAction is a tool-based action, not an InteractAction.
It is typically called by InteractActions via the InteractWalker.
"""

# Import endpoints for automatic discovery
from jvagent.action.persona import endpoints  # noqa: F401
from jvagent.action.persona.persona_action import PersonaAction

__all__ = [
    "PersonaAction",
]
