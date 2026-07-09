"""API key scope helper for PageIndex jvforge LLM webhook URLs.

Inbound route path remains ``.../pageindex_retrieval_interact_action/interact/webhook/{agent_id}``;
credentials are persisted on ``PageIndexAction``.
"""

from jvagent.action.utils.webhook_system_user import webhook_system_user_factory

SYSTEM_USER_EMAIL = "pageindex-retrieval-interact-action-service@system.internal"
WEBHOOK_PERMISSION = "webhook:pageindex_retrieval_interact_action"
PAGEINDEX_WEBHOOK_ROUTE_PREFIX = "pageindex_retrieval_interact_action/interact/webhook"
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
