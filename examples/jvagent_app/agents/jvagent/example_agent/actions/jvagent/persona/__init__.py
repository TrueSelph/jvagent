"""Persona Action Package

This package provides the PersonaAction class for agent behavioral modeling.

The PersonaAction is a simplified tool-based action that:
- Applies agent prompts with configurable parameters
- Composes system prompts from persona attributes
- Integrates with ModelAction for LLM queries
- Provides a simple respond() method interface

Note: PersonaAction is a tool-based action, not an InteractAction.
It is typically called by InteractActions via the InteractWalker.
"""

# Import the action class so it can be imported from the package
from .persona import ExamplePersonaAction

# Import endpoints module to ensure endpoints are discovered
from jvagent.action.persona import endpoints  # noqa: F401

__all__ = ["ExamplePersonaAction"]

