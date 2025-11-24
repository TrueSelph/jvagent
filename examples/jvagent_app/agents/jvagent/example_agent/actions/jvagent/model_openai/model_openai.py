"""OpenAI Model Action for jvagent example agent.

This module is required by the action loader. The loader expects:
- A file named {action_name}.py (from package.name in info.yaml)
- A class matching the archetype name (OpenAIModelAction from info.yaml)

The action loader will:
1. Discover this file based on package.name: "jvagent/model_openai" -> "model_openai.py"
2. Load this module and find the class matching archetype: "OpenAIModelAction"
3. Instantiate the action with configuration from agent.yaml

The OpenAIModelAction implementation is in jvagent.action.model.openai (core package).
This file simply re-exports it for the action loader to discover.

Note: Endpoints are defined in jvagent.action.model.endpoints and are automatically
discovered when the jvagent package is imported (they're imported in __init__.py).
"""

# Import the implementation from core
from jvagent.action.model.openai import OpenAIModelAction

# Export for action loader (must match archetype in info.yaml)
__all__ = ["OpenAIModelAction"]

