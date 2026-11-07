"""API key scope helper for PageIndex jvforge LLM webhook URLs.

Inbound route: ``/api/pageindex/interact/webhook/{agent_id}``.
Credentials are persisted on ``PageIndexAction``.
"""

from typing import Any

from jvspatial.api.exceptions import AuthenticationError, AuthorizationError

from jvagent.action.utils.webhook_system_user import webhook_system_user_factory

SYSTEM_USER_EMAIL = "pageindex-retrieval-interact-action-service@system.internal"
WEBHOOK_PERMISSION = "webhook:pageindex"
PAGEINDEX_WEBHOOK_ROUTE_PREFIX = "pageindex/interact/webhook"
ALLOWED_WEBHOOK_ENDPOINT_GLOB = f"/api/{PAGEINDEX_WEBHOOK_ROUTE_PREFIX}/*"

get_or_create_system_user = webhook_system_user_factory(
    SYSTEM_USER_EMAIL, WEBHOOK_PERMISSION
)


def webhook_actor(principal: Any, expected_key_id: str | None) -> str:
    """Bind middleware-authenticated credentials to the target Action."""
    if not isinstance(principal, dict):
        raise AuthenticationError("A PageIndex webhook credential is required")
    actor = principal.get("user_id")
    permissions = principal.get("permissions")
    if (
        not isinstance(actor, str)
        or not actor
        or not expected_key_id
        or principal.get("api_key_id") != expected_key_id
        or not isinstance(permissions, (list, tuple))
        or WEBHOOK_PERMISSION not in permissions
    ):
        raise AuthorizationError(
            "Webhook credential is not bound to this PageIndex Action"
        )
    return actor


__all__ = [
    "get_or_create_system_user",
    "SYSTEM_USER_EMAIL",
    "WEBHOOK_PERMISSION",
    "PAGEINDEX_WEBHOOK_ROUTE_PREFIX",
    "ALLOWED_WEBHOOK_ENDPOINT_GLOB",
]
