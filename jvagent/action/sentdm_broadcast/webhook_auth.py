"""Webhook authentication utilities for the SentDM broadcast action."""

from jvagent.action.utils.webhook_system_user import (
    get_or_create_system_user_for_webhook,
)

SYSTEM_USER_EMAIL = "sentdm-service@system.internal"
_WEBHOOK_PERMISSION = "webhook:sentdm"


async def get_or_create_system_user() -> str:
    """Get or create system service user for SentDM webhook API keys."""
    return await get_or_create_system_user_for_webhook(
        SYSTEM_USER_EMAIL, _WEBHOOK_PERMISSION
    )


__all__ = ["get_or_create_system_user", "SYSTEM_USER_EMAIL"]
