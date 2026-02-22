"""Action system for jvagent.

This module provides the action management system including:
- Actions: Manager node for action registration and discovery
- Action: Base action class for all pluggable actions with CRUD endpoints
- ActionLoader: Dynamic action discovery and loading
"""

from jvagent.action.actions import Actions
from jvagent.action.base import Action


def __getattr__(name: str):
    """Lazy-import heavy loader objects or submodules to avoid circular imports."""
    if name in ("ActionLoader", "ActionMetadata"):
        from jvagent.action.action_loader import ActionLoader, ActionMetadata

        return ActionLoader if name == "ActionLoader" else ActionMetadata

    if name == "interact":
        import importlib

        return importlib.import_module("jvagent.action.interact")

    if name == "endpoints":
        import importlib

        return importlib.import_module("jvagent.action.endpoints")

    raise AttributeError(name)


__all__ = ["Actions", "Action", "ActionLoader", "ActionMetadata"]
