"""InteractAction base class for pluggable interact subsystem.

This module provides the InteractAction base class for actions that participate
in the interact subsystem traversal via InteractWalker.
"""

# Import endpoints module to ensure endpoints are discovered
from jvagent.action.interact import endpoints  # noqa: F401
from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import (
    InteractionInitResult,
    InteractWalker,
)

__all__ = ["InteractAction", "InteractWalker", "InteractionInitResult"]
