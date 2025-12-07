"""InteractRouter Action Package

This package provides the InteractRouter class for intent-based routing of interactions.

The InteractRouter is a specialized InteractAction that:
- Runs first (negative weight) to analyze incoming utterances
- Uses an LLM to generate interpretations and match against anchor statements
- Routes to appropriate InteractActions based on intent
- Stores routing results on the Interaction node

Note: This is a stub that imports the actual implementation from the core jvagent package.
The implementation is in jvagent.action.router.interact_router.
"""

# Import the action class so it can be imported from the package
from .interact_router import InteractRouter

__all__ = ["InteractRouter"]

