"""System user for PageIndexRetrievalInteractAction webhook API keys."""

from jvagent.action.utils.webhook_system_user import (
    get_or_create_system_user_for_webhook,
)

SYSTEM_USER_EMAIL = "pageindex-retrieval-interact-action-service@system.internal"
_WEBHOOK_PERMISSION = "webhook:pageindex_retrieval_interact_action"


async def get_or_create_system_user() -> str:
    """Return the system user id used to own PageIndex LLM webhook API keys."""
    return await get_or_create_system_user_for_webhook(
        SYSTEM_USER_EMAIL, _WEBHOOK_PERMISSION
    )


__all__ = ["get_or_create_system_user", "SYSTEM_USER_EMAIL"]
