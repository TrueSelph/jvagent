"""OpenAI Model Action Package

This package provides the OpenAIModelAction class.

Note: API endpoints are defined in the core jvagent.action.model.endpoints module
and are automatically discovered when the jvagent package is imported.
"""

# Import the action class so it can be imported from the package
from .model_openai import OpenAIModelAction

__all__ = ["OpenAIModelAction"]
