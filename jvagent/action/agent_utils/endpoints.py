"""Agent Utils endpoints."""

import logging
from typing import Dict, List

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError

from .agent_utils import AgentUtils

logger = logging.getLogger(__name__)


@endpoint(
    "/actions/{action_id}/interactions",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["AgentUtils"],
    response=success_response(
        data={
            "interactions": ResponseField(
                field_type=List[Dict],
                description="List of interaction log contents",
                example=[{"id": "o.DBLog.123", "entity": "DBLog", "context": {}}],
            ),
        }
    ),
)
async def list_interactions_endpoint(
    action_id: str,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, List[Dict]]:
    """List interaction log contents (most-recent first, paginated).

    **Args:**

    - action_id: AgentUtils action ID
    - limit: Max number of interactions to return (default 100, max 1000)
    - offset: Skip the first N most-recent interactions (default 0)

    **Returns:**

    Dictionary with list of interaction log contents.

    AUDIT-actions XC-19: pagination + path containment + thread-offload
    are enforced inside :meth:`AgentUtils.list_interactions`.
    """
    action = await AgentUtils.get(action_id)
    if not action or not isinstance(action, AgentUtils):
        raise ResourceNotFoundError(f"AgentUtils action not found: {action_id}")

    interactions = await action.list_interactions(limit=limit, offset=offset)

    return {
        "interactions": interactions,
    }
