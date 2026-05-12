"""SentDM broadcast action package.

Provides a thin wrapper around the SentDM v3 REST API for sending template-based
SMS / WhatsApp broadcasts and receiving delivery-status webhooks.
"""

from . import endpoints  # noqa: F401  (import for endpoint registration)
from .sentdm_broadcast_action import SentDMBroadcastAction

__all__ = ["SentDMBroadcastAction"]
