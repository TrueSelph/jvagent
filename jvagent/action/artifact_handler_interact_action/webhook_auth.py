"""API key scope helper for artifact_handler jvforge notify webhook URLs.

Inbound route: ``/api/artifact_handler_action/notify/{agent_id}``.
Credentials are persisted on ``ArtifactHandlerInteractAction``.

Keys are minted with ``ALLOWED_WEBHOOK_ENDPOINT_GLOB`` (``…/notify/*``),
same as Drive/WhatsApp/PageIndex. Cross-agent binding is the handler hmac
of ``notify_webhook_api_key_id``.
"""

from jvagent.action.utils.webhook_system_user import webhook_system_user_factory

SYSTEM_USER_EMAIL = "artifact-handler-action-service@system.internal"
WEBHOOK_PERMISSION = "webhook:artifact_handler_action"
ARTIFACT_HANDLER_NOTIFY_ROUTE_PREFIX = "artifact_handler_action/notify"


def notify_endpoint_for_agent(agent_id: str) -> str:
    """Exact allowed_endpoints entry for one agent's notify callback."""
    aid = (agent_id or "").strip()
    return f"/api/{ARTIFACT_HANDLER_NOTIFY_ROUTE_PREFIX}/{aid}"


# Legacy name kept for importers; prefer :func:`notify_endpoint_for_agent`.
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
    "notify_endpoint_for_agent",
]
