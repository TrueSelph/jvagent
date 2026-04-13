"""Access Control action package.

This module provides role-based access control for agent operations.
"""

# Import endpoints to register them
from . import endpoints  # noqa: F401
from .access_control_action import AccessControlAction

__all__ = ["AccessControlAction"]
