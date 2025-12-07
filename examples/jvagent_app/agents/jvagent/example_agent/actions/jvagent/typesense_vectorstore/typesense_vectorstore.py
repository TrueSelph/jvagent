"""TypesenseVectorStore Action for jvagent example agent.

This module is required by the action loader. The loader expects:
- A file named {action_name}.py (from package.name in info.yaml)
- A class matching the archetype name (TypesenseVectorStore from info.yaml)

The action loader will:
1. Discover this file based on package.name: "jvagent/typesense_vectorstore" -> "typesense_vectorstore.py"
2. Load this module and find the class matching archetype: "TypesenseVectorStore"
3. Instantiate the action with configuration from agent.yaml

The TypesenseVectorStore implementation is in jvagent.action.vectorstore.typesense (core package).
This file simply re-exports it for the action loader to discover.

Note: This is a stub that delegates to the core implementation.
"""

# Import the implementation from core
from jvagent.action.vectorstore.typesense import TypesenseVectorStore

# Export for action loader (must match archetype in info.yaml)
__all__ = ["TypesenseVectorStore"]

