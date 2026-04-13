"""Facebook action package.

Provides FacebookAction, FacebookAPI, and associated API endpoints.
"""

from . import endpoints  # noqa: F401
from .facebook_action import FacebookAction

__all__ = ["FacebookAction"]
