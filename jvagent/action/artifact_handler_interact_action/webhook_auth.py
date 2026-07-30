"""API key scope helper for artifact_handler jvforge notify webhook URLs.

Inbound route: ``/api/artifact_handler_action/notify/{agent_id}``.
Credentials are persisted on ``ArtifactHandlerInteractAction``.
"""

from jvagent.action.utils.webhook_system_user import webhook_system_user_factory

SYSTEM_USER_EMAIL = "artifact-handler-action-service@system.internal"
WEBHOOK_PERMISSION = "webhook:artifact_handler_action"
ARTIFACT_HANDLER_NOTIFY_ROUTE_PREFIX = "artifact_handler_action/notify"
ALLOWED_WEBHOOK_ENDPOINT_GLOB = f"/api/{ARTIFACT_HANDLER_NOTIFY_ROUTE_PREFIX}/*"

get_or_create_system_user = webhook_system_user_factory(
    SYSTEM_USER_EMAIL, WEBHOOK_PERMISSION
)

__all__ = [
    "get_or_create_system_user",
    "SYSTEM_USER_EMAIL",
    "WEBHOOK_PERMISSION",
    "ARTIFACT_HANDLER_NOTIFY_ROUTE_PREFIX",
    "ALLOWED_WEBHOOK_ENDPOINT_GLOB",
]
