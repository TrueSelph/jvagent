"""InteractRouter Action for jvagent example agent.

This module is required by the action loader. The loader expects:
- A file named {action_name}.py (from package.name in info.yaml)
- A class matching the archetype name (InteractRouter from info.yaml)

The action loader will:
1. Discover this file based on package.name: "jvagent/interact_router" -> "interact_router.py"
2. Load this module and find the class matching archetype: "InteractRouter"
3. Instantiate the action with configuration from agent.yaml

The InteractRouter implementation is in jvagent.action.router.interact_router (core package).
This file simply re-exports it for the action loader to discover.

Note: This is a stub that delegates to the core implementation.
"""

# Import the implementation from core
from jvagent.action.router.interact_router import InteractRouter

# Export for action loader (must match archetype in info.yaml)
__all__ = ["InteractRouter"]

