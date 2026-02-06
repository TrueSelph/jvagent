"""Action system for jvagent.

This module provides the action management system including:
- Actions: Manager node for action registration and discovery
- Action: Base action class for all pluggable actions with CRUD endpoints
- ActionLoader: Dynamic action discovery and loading
"""

from jvagent.action.base import Action
from jvagent.action.actions import Actions
from jvagent.action import interact  # noqa: F401
from jvagent.action import endpoints  # noqa: F401 - Import to register endpoints


def __getattr__(name: str):
	# Lazy-import heavy loader objects to avoid circular imports at package import time
	if name in ("ActionLoader", "ActionMetadata"):
		from jvagent.action.action_loader import ActionLoader, ActionMetadata
		return ActionLoader if name == "ActionLoader" else ActionMetadata
	raise AttributeError(name)


__all__ = ["Actions", "Action", "ActionLoader", "ActionMetadata"]
