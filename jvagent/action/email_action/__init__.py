"""Email action: Gmail, Outlook (OAuth + inbound webhook), or SendGrid (API key + webhook)."""

from . import endpoints  # noqa: F401
from .email_action import EmailAction

__all__ = ["EmailAction"]
