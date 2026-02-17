"""Memory admin endpoints for purge operations."""

from typing import Any, Dict, List, Optional

from fastapi import Query

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError

from jvagent.core.agent import Agent
from jvagent.memory.manager import Memory


@endpoint(
    "/api/agents/{agent_id}/memory/purge",
    methods=["DELETE"],
    auth=True,
    roles=["admin"],
    tags=["Memory"],
    response=success_response(
        data={
            "purged_count": ResponseField(
                field_type=int,
                description="Number of conversations purged",
                example=5,
            ),
            "message": ResponseField(
                field_type=str,
                description="Success message",
                example="Purged 5 conversation(s) and cleaned orphaned interactions",
            ),
        }
    ),
)
async def purge_conversations(
    agent_id: str,
    user_id: Optional[str] = Query(None, description="Purge only this user's conversations"),
    conversation_id: Optional[str] = Query(None, description="Purge only this conversation"),
) -> Dict[str, Any]:
    """Purge conversations for an agent (admin only).

    Requires authentication with admin role. Purges conversations and
    orphaned interactions. Orphan cleanup runs automatically as the final step.

    Args:
        agent_id: ID of the agent whose memory to purge
        user_id: Optional - purge only this user's conversations
        conversation_id: Optional - purge only this conversation (user_id ignored)

    Returns:
        Dictionary with purged_count and message

    Raises:
        ResourceNotFoundError: If agent or memory not found
    """
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID '{agent_id}' not found",
            details={"agent_id": agent_id},
        )

    memory = await agent.get_memory()
    if not memory:
        raise ResourceNotFoundError(
            message=f"Memory not found for agent '{agent_id}'",
            details={"agent_id": agent_id},
        )

    purged: Optional[List] = await memory.purge_conversations(
        user_id=user_id,
        conversation_id=conversation_id,
    )

    count = len(purged) if purged else 0
    return {
        "purged_count": count,
        "message": (
            f"Purged {count} conversation(s) and cleaned orphaned interactions"
        ),
    }
