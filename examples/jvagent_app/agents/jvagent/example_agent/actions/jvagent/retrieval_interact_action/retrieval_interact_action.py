"""RetrievalInteractAction Action for jvagent example agent.

This module is required by the action loader. The loader expects:
- A file named {action_name}.py (from package.name in info.yaml)
- A class matching the archetype name (RetrievalInteractAction from info.yaml)

The action loader will:
1. Discover this file based on package.name: "jvagent/retrieval_interact_action" -> "retrieval_interact_action.py"
2. Load this module and find the class matching archetype: "RetrievalInteractAction"
3. Instantiate the action with configuration from agent.yaml

The RetrievalInteractAction implementation is in jvagent.action.retrieval.retrieval_interact_action (core package).
This file simply re-exports it for the action loader to discover.

Note: This is a stub that delegates to the core implementation.
"""

# Import the implementation from core
from jvagent.action.retrieval.retrieval_interact_action import RetrievalInteractAction

# Export for action loader (must match archetype in info.yaml)
__all__ = ["RetrievalInteractAction"]

