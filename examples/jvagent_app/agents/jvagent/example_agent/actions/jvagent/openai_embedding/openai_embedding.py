"""OpenAI Embedding Model Action for jvagent example agent.

This module is required by the action loader. The loader expects:
- A file named {action_name}.py (from package.name in info.yaml)
- A class matching the archetype name (OpenAIEmbeddingModelAction from info.yaml)

The action loader will:
1. Discover this file based on package.name: "jvagent/openai_embedding" -> "openai_embedding.py"
2. Load this module and find the class matching archetype: "OpenAIEmbeddingModelAction"
3. Instantiate the action with configuration from agent.yaml

The OpenAIEmbeddingModelAction implementation is in jvagent.action.model.embedding.openai (core package).
This file simply re-exports it for the action loader to discover.

Note: Endpoints are defined in jvagent.action.model.embedding.endpoints and are automatically
discovered when the jvagent package is imported (they're imported in __init__.py).
"""

# Import the implementation from core
from jvagent.action.model.embedding.openai import OpenAIEmbeddingModelAction

# Export for action loader (must match archetype in info.yaml)
__all__ = ["OpenAIEmbeddingModelAction"]

