"""Webhook authentication utilities for the SentDM broadcast action."""

from jvagent.action.utils.webhook_system_user import webhook_system_user_factory

SYSTEM_USER_EMAIL = "sentdm-service@system.internal"
_WEBHOOK_PERMISSION = "webhook:sentdm"

get_or_create_system_user = webhook_system_user_factory(
    SYSTEM_USER_EMAIL, _WEBHOOK_PERMISSION
)

__all__ = ["get_or_create_system_user", "SYSTEM_USER_EMAIL"]
