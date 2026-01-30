"""Access Control action package.

This module provides role-based access control for agent operations.
"""

from .access_control_action import AccessControlAction

# Import endpoints to register them
from . import endpoints  # noqa: F401

__all__ = ["AccessControlAction"]
