"""API key scope helper for PageIndex jvforge LLM webhook URLs.

Inbound route: ``/api/pageindex/interact/webhook/{agent_id}``.
Credentials are persisted on ``PageIndexAction``.
"""

from jvagent.action.utils.webhook_system_user import webhook_system_user_factory

SYSTEM_USER_EMAIL = "pageindex-retrieval-interact-action-service@system.internal"
WEBHOOK_PERMISSION = "webhook:pageindex"
PAGEINDEX_WEBHOOK_ROUTE_PREFIX = "pageindex/interact/webhook"
ALLOWED_WEBHOOK_ENDPOINT_GLOB = f"/api/{PAGEINDEX_WEBHOOK_ROUTE_PREFIX}/*"

get_or_create_system_user = webhook_system_user_factory(
    SYSTEM_USER_EMAIL, WEBHOOK_PERMISSION
)

__all__ = [
    "get_or_create_system_user",
    "SYSTEM_USER_EMAIL",
    "WEBHOOK_PERMISSION",
    "PAGEINDEX_WEBHOOK_ROUTE_PREFIX",
    "ALLOWED_WEBHOOK_ENDPOINT_GLOB",
]
