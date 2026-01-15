"""WhatsApp action package.

This module provides WhatsApp integration.
"""

from .whatsapp import Whatsapp
from .whatsapp_adapter import WhatsAppAdapter

# Import endpoints for automatic discovery
from jvagent.action.whatsapp import endpoints  # noqa: F401

__all__ = ["Whatsapp", "WhatsAppAdapter"]
