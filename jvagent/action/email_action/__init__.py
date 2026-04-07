"""Email action: Gmail (OAuth + poll) or SendGrid (API key + webhook)."""

from . import endpoints  # noqa: F401
from .email_action import EmailAction

__all__ = ["EmailAction"]
