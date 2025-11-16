"""Action system for jvagent.

This module provides the action management system including:
- Actions: Manager node for action registration and discovery
- Action: Base action class for all pluggable actions
"""

from jvagent.action.actions import Actions
from jvagent.action.action import Action

__all__ = ["Actions", "Action"]

