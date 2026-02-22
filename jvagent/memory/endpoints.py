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
                example="Purged 5 conversation(s)",
            ),
        }
    ),
)
async def purge_conversations(
    agent_id: str,
    user_id: Optional[str] = Query(
        None, description="Purge only this user's conversations"
    ),
    conversation_id: Optional[str] = Query(
        None, description="Purge only this conversation"
    ),
) -> Dict[str, Any]:
    """Purge conversations for an agent (admin only).

    Requires authentication with admin role. Purges conversations (cascade
    deletes interactions). Does not run repair; call repair endpoint separately.

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
        "message": f"Purged {count} conversation(s)",
    }


@endpoint(
    "/api/agents/{agent_id}/memory/repair",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Memory"],
    response=success_response(
        data={
            "orphaned_interactions_deleted": ResponseField(
                field_type=int,
                description="Number of orphaned interactions deleted",
                example=3,
            ),
            "orphaned_users_reconnected": ResponseField(
                field_type=int,
                description="Number of orphaned users reconnected",
                example=1,
            ),
            "dual_edges_removed": ResponseField(
                field_type=int,
                description="Number of duplicate interaction chain edges removed",
                example=0,
            ),
            "conversation_first_edges_restored": ResponseField(
                field_type=int,
                description="Number of conversation-to-first-interaction edges restored",
                example=0,
            ),
            "message": ResponseField(
                field_type=str,
                description="Success message",
                example="Repair completed: 3 orphaned interaction(s) deleted, 1 user(s) reconnected",
            ),
        }
    ),
)
async def repair_memory(
    agent_id: str,
    recent_minutes: Optional[int] = Query(
        None,
        description="Only clean orphan interactions from last N minutes (None = all)",
    ),
) -> Dict[str, Any]:
    """Run memory repair for an agent (admin only, manually triggered).

    Deletes orphaned interactions, repairs dual edges and missing conv->first
    edges, and reconnects orphaned users. No automatic triggers; invoke explicitly.

    Args:
        agent_id: ID of the agent whose memory to repair
        recent_minutes: Optional - only clean orphan interactions from last N minutes

    Returns:
        Dictionary with orphaned_interactions_deleted, orphaned_users_reconnected,
        dual_edges_removed, conversation_first_edges_restored, message

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

    result = await memory.repair_memory(recent_minutes=recent_minutes)
    deleted = result["orphaned_interactions_deleted"]
    reconnected = result["orphaned_users_reconnected"]
    dual_removed = result["dual_edges_removed"]
    first_restored = result["conversation_first_edges_restored"]
    return {
        "orphaned_interactions_deleted": deleted,
        "orphaned_users_reconnected": reconnected,
        "dual_edges_removed": dual_removed,
        "conversation_first_edges_restored": first_restored,
        "message": (
            f"Repair completed: {deleted} orphaned interaction(s) deleted, "
            f"{reconnected} user(s) reconnected, {dual_removed} dual edge(s) removed, "
            f"{first_restored} conv-first edge(s) restored"
        ),
    }
