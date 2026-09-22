"""Notify webhook path helper for artifact_handler jvforge callbacks.

Inbound route: ``/api/artifact_handler_action/notify/{agent_id}``.
No API key is minted; jvforge POSTs this path with a known job_id and
trusted ``/v1/artifacts/{job_id}`` URL.
"""

ARTIFACT_HANDLER_NOTIFY_ROUTE_PREFIX = "artifact_handler_action/notify"


def notify_endpoint_for_agent(agent_id: str) -> str:
    """Notify callback path for one agent."""
    aid = (agent_id or "").strip()
    return f"/api/{ARTIFACT_HANDLER_NOTIFY_ROUTE_PREFIX}/{aid}"


__all__ = [
    "ARTIFACT_HANDLER_NOTIFY_ROUTE_PREFIX",
    "notify_endpoint_for_agent",
]
