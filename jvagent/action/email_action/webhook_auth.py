"""Webhook API key ownership for EmailAction."""

from jvagent.action.utils.webhook_system_user import (
    get_or_create_system_user_for_webhook,
)

SYSTEM_USER_EMAIL = "email-service@system.internal"
_WEBHOOK_PERMISSION = "webhook:email"


async def get_or_create_system_user() -> str:
    """System user for Email inbound webhook API keys."""
    return await get_or_create_system_user_for_webhook(
        SYSTEM_USER_EMAIL, _WEBHOOK_PERMISSION
    )


__all__ = ["get_or_create_system_user", "SYSTEM_USER_EMAIL"]
