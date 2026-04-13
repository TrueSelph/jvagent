"""Avatar action package.

Provides AvatarAction and associated API endpoints.
"""

# Import endpoints to ensure they are discovered and registered
from . import endpoints  # noqa: F401
from .avatar_action import AvatarAction

__all__ = ["AvatarAction"]
