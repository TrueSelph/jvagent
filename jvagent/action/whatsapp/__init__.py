"""WhatsApp action package.

This module provides WhatsApp integration with enhanced security,
error handling, and compliance with jvspatial coding standards.
"""

# Import endpoints for automatic discovery
from . import endpoints  # noqa: F401
from .whatsapp_action import WhatsAppAction
from .whatsapp_adapter import WhatsAppAdapter

__all__ = ["WhatsAppAction", "WhatsAppAdapter"]
