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
) -> Dict[str, List[Dict]]:
    """List all interaction log contents.

    **Args:**

    - action_id: AgentUtils action ID

    **Returns:**

    Dictionary with list of interaction log contents
    """
    action = await AgentUtils.get(action_id)
    if not action or not isinstance(action, AgentUtils):
        raise ResourceNotFoundError(f"AgentUtils action not found: {action_id}")

    interactions = await action.list_interactions()

    return {
        "interactions": interactions,
    }
