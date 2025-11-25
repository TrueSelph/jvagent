"""Persona Action Package

This package provides the PersonaAction class for agent behavioral modeling.

The PersonaAction is a core interact action that:
- Processes user interactions via LLM-driven parameters
- Supports canned responses for quick replies
- Delegates to helper actions for complex tasks
- Uses an event bus for asynchronous response handling

Note: API endpoints are defined in the core jvagent.action.persona.endpoints module
and are automatically discovered when the jvagent package is imported.
"""

# Import the action class so it can be imported from the package
from .persona import ExamplePersonaAction

# Import endpoints module to ensure endpoints are discovered
from jvagent.action.persona import endpoints  # noqa: F401

__all__ = ["ExamplePersonaAction"]

