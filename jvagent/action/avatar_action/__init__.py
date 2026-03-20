"""Avatar action package.

Provides AvatarAction and associated API endpoints.
"""

from .avatar_action import AvatarAction

# Import endpoints to ensure they are discovered and registered
from . import endpoints  # noqa: F401

__all__ = ["AvatarAction"]
