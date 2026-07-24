"""InteractAction base class for pluggable interact subsystem.

This module provides the InteractAction base class for actions that participate
in the interact subsystem traversal via InteractWalker.
"""

# Import endpoints modules to ensure their @endpoint routes are discovered.
from jvagent.action.interact import avatar_endpoints  # noqa: F401
from jvagent.action.interact import endpoints  # noqa: F401
from jvagent.action.interact import upload_endpoints  # noqa: F401
from jvagent.action.interact import voice_endpoints  # noqa: F401
from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker

__all__ = ["InteractAction", "InteractWalker"]
