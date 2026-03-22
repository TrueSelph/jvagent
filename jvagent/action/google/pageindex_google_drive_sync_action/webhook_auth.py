"""Webhook authentication utilities for PageIndexGoogleDriveSync action."""

from jvagent.action.utils.webhook_system_user import (
    get_or_create_system_user_for_webhook,
)

SYSTEM_USER_EMAIL = "pageindex-google-drive-sync-service@system.internal"
_WEBHOOK_PERMISSION = "webhook:pageindex_google_drive_sync_action"


async def get_or_create_system_user() -> str:
    """Get or create system service user for PageIndex Drive sync webhook API keys."""
    return await get_or_create_system_user_for_webhook(
        SYSTEM_USER_EMAIL, _WEBHOOK_PERMISSION
    )


__all__ = ["get_or_create_system_user", "SYSTEM_USER_EMAIL"]
