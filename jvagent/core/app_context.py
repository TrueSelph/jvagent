"""Global application context for sharing state across modules.

This module provides a centralized place to store application-level
configuration that needs to be accessed by various components before
the full app is bootstrapped.
"""

import os
from typing import Optional

# Global app root path - set by CLI before server starts
_app_root: Optional[str] = None


def set_app_root(path: str) -> None:
    """Set the application root path.
    
    This should be called by the CLI before any config loading occurs.
    
    Args:
        path: Absolute path to the application root directory
    """
    global _app_root
    _app_root = os.path.abspath(path)


def get_app_root() -> str:
    """Get the application root path.
    
    Returns:
        The app root path if set, otherwise the current working directory
    """
    return _app_root or os.getcwd()


def clear_app_root() -> None:
    """Clear the app root path (useful for testing)."""
    global _app_root
    _app_root = None
