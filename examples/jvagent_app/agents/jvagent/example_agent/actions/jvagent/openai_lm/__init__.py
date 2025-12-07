"""OpenAI Model Action Package

This package provides the OpenAILanguageModelAction class.

Note: API endpoints are defined in the core jvagent.action.model.language.endpoints module
and are automatically discovered when the jvagent package is imported.
"""

# Import the action class so it can be imported from the package
from .openai_lm import OpenAILanguageModelAction

__all__ = ["OpenAILanguageModelAction"]
