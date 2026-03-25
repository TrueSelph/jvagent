"""Memory admin endpoints for purge operations and user lookup."""

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import Query
from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError
from jvspatial.logging.filter_utils import validate_log_filter

from jvagent.core.agent import Agent
from jvagent.memory.manager import Memory
from jvagent.memory.user import User
from jvagent.memory.user_long_memory import UserLongMemory

logger = logging.getLogger(__name__)


def _user_context_matches(user: User, filter_query: Dict[str, Any]) -> bool:
    """Check if a User's context matches the MongoDB-style filter."""
    if not filter_query:
        return True
    ctx = {
        "user_id": user.user_id,
        "name": user.name,
        "display_name": user.display_name,
        "user_model": user.user_model,
        "usage": user.usage,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_seen": user.last_seen.isoformat() if user.last_seen else None,
    }
    for key, expected in filter_query.items():
        if not key.startswith("context."):
            continue
        attr = key.replace("context.", "", 1)
        val = ctx.get(attr)
        if isinstance(expected, dict):
            if "$in" in expected:
                if val not in expected["$in"]:
                    return False
            elif "$eq" in expected:
                if val != expected["$eq"]:
                    return False
            elif "$ne" in expected:
                if val == expected["$ne"]:
                    return False
            elif "$regex" in expected:
                import re

                if not isinstance(val, str) or not re.search(expected["$regex"], val):
                    return False
            elif "$exists" in expected:
                exists = val is not None
                if expected["$exists"] != exists:
                    return False
        else:
            if val != expected:
                return False
    return True


@endpoint(
    "/api/agents/{agent_id}/memory/users",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Memory"],
    response=success_response(
        data={
            "users": ResponseField(
                field_type=list,
                description="Paginated list of full User node records (id, entity, context)",
            ),
            "pagination": ResponseField(
                field_type=Dict[str, Any],
                description="Pagination metadata (page, page_size, total, total_pages)",
            ),
        }
    ),
)
async def get_users(
    agent_id: str,
    filter: Optional[str] = Query(
        None,
        description='MongoDB-style filter JSON (e.g. {"context.user_id":{"$in":["id1","id2"]}})',
    ),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(50, ge=1, le=200, description="Items per page (max 200)"),
) -> Dict[str, Any]:
    """List User nodes from an agent's memory with pagination and filter (admin only).

    Requires authentication with admin role. Returns full User node records (id, entity, context, edges). Supports
    MongoDB-style filter for context.user_id, context.name, and other context fields.

    **Path Parameters:**
    - `agent_id`: Agent node ID (required)

    **Query Parameters:**
    - `filter`: Optional MongoDB-style filter JSON. Keys must use context. prefix.
      Examples: {"context.user_id":{"$in":["id1","id2"]}}, {"context.name":"John"}
    - `page`: Page number (default: 1)
    - `page_size`: Items per page (default: 50, max: 200)

    **Returns:**
    - `users`: List of full User node records
    - `pagination`: { page, page_size, total, total_pages }

    **Raises:**
    - ResourceNotFoundError: If agent or memory not found
    - ValidationError: If filter JSON is invalid
    """
    filter_query: Optional[Dict[str, Any]] = None
    if filter:
        try:
            filter_dict = json.loads(filter)
        except json.JSONDecodeError as e:
            raise ValidationError(
                message=f"Invalid filter JSON: {e}",
                details={"filter": filter},
            ) from e
        if not isinstance(filter_dict, dict):
            raise ValidationError(
                message="Filter must be a JSON object",
                details={"filter": filter},
            )
        filter_query = validate_log_filter(filter_dict)

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

    try:
        all_users = await memory.get_users()
        filtered = [
            u for u in all_users if _user_context_matches(u, filter_query or {})
        ]
        filtered.sort(key=lambda u: u.last_seen or u.created_at, reverse=True)

        total = len(filtered)
        total_pages = max(1, (total + page_size - 1) // page_size)
        start = (page - 1) * page_size
        page_users = filtered[start : start + page_size]

        users_data: List[Dict[str, Any]] = []
        for user in page_users:
            exported = await user.export()
            users_data.append(exported)
    except Exception as e:
        logger.warning("Failed to list users: %s", e)
        users_data = []
        total = 0
        total_pages = 0

    return {
        "users": users_data,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
        },
    }


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
    "/api/agents/{agent_id}/memory/users/{user_id}",
    methods=["DELETE"],
    auth=True,
    roles=["admin"],
    tags=["Memory"],
    response=success_response(
        data={
            "deleted_count": ResponseField(
                field_type=int,
                description="Number of user nodes deleted",
                example=1,
            ),
            "message": ResponseField(
                field_type=str,
                description="Success message",
                example="Deleted user 'user123' and all connected nodes",
            ),
        }
    ),
)
async def delete_user_memory(
    agent_id: str,
    user_id: str,
) -> Dict[str, Any]:
    """Delete a user node and all connected nodes beneath it (admin only).

    Requires authentication with admin role. Deletes the User node and cascades
    to all connected nodes: Conversations, Interactions, SubscriptionSettings,
    and any other nodes solely reachable from the user.

    Args:
        agent_id: ID of the agent whose memory to modify
        user_id: External user identifier to delete

    Returns:
        Dictionary with deleted_count and message

    Raises:
        ResourceNotFoundError: If agent, memory, or user not found
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

    purged = await memory.purge_user_memory(user_id=user_id)
    if not purged:
        raise ResourceNotFoundError(
            message=f"User '{user_id}' not found in memory",
            details={"agent_id": agent_id, "user_id": user_id},
        )

    count = len(purged)
    return {
        "deleted_count": count,
        "message": f"Deleted user '{user_id}' and all connected nodes",
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
            "conversation_branch_edges_removed": ResponseField(
                field_type=int,
                description="Number of conversation-branch edges removed (extra conv->interaction edges)",
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
    conv_branch_removed = result["conversation_branch_edges_removed"]
    return {
        "orphaned_interactions_deleted": deleted,
        "orphaned_users_reconnected": reconnected,
        "dual_edges_removed": dual_removed,
        "conversation_first_edges_restored": first_restored,
        "conversation_branch_edges_removed": conv_branch_removed,
        "message": (
            f"Repair completed: {deleted} orphaned interaction(s) deleted, "
            f"{reconnected} user(s) reconnected, {dual_removed} dual edge(s) removed, "
            f"{first_restored} conv-first edge(s) restored, "
            f"{conv_branch_removed} conv-branch edge(s) removed"
        ),
    }


@endpoint(
    "/api/agents/{agent_id}/memory/me",
    methods=["GET"],
    auth=True,
    tags=["Memory"],
    response=success_response(
        data={
            "memory": ResponseField(
                field_type=dict,
                description="Your long-term memory structured by category",
            ),
        }
    ),
)
async def get_my_memory(
    agent_id: str,
    user_id: Optional[str] = Query(None, description="Caller's user_id (from client storage)"),
) -> Dict[str, Any]:
    """Get the current user's long-term memory for an agent.

    Returns memory categories for the requesting user. Any authenticated user
    can call this endpoint to see their own stored profile data.

    **Args:**
    - `agent_id`: Agent node ID
    - `user_id`: The caller's user identifier (passed as query param from the client)

    **Returns:**
    - `memory`: { category_key: { title, content, updated_at } }
    """
    if not user_id:
        return {"memory": {}}
        
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(f"Agent with ID '{agent_id}' not found")

    memory_manager = await agent.get_memory()
    if not memory_manager:
        raise ResourceNotFoundError(f"Memory not found for agent '{agent_id}'")

    user = await memory_manager.get_user(user_id)
    if not user:
        return {"memory": {}}

    long_memory = await UserLongMemory.get_for_user(user)
    if not long_memory:
        return {"memory": {}}

    content_map = {}
    categories = await long_memory.get_all_categories()
    for cat in categories:
        if not cat.is_empty():
            content_map[cat.category] = {
                "title": cat.title,
                "content": cat.content,
                "updated_at": cat.updated_at.isoformat() if cat.updated_at else None,
            }

    return {"memory": content_map}


@endpoint(
    "/api/agents/{agent_id}/memory/users/{user_id}/content",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Memory"],
    response=success_response(
        data={
            "memory": ResponseField(
                field_type=dict,
                description="User's long-term memory structured by category",
            ),
        }
    ),
)
async def get_user_memory_content(
    agent_id: str,
    user_id: str,
) -> Dict[str, Any]:
    """Get a specific user's long-term memory for an agent (admin only).

    Returns a dictionary mapping category titles to their markdown content.

    **Args:**
    - `agent_id`: Agent node ID
    - `user_id`: Target user's identifier

    **Returns:**
    - `memory`: { category_title: content }
    """
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(f"Agent with ID '{agent_id}' not found")

    memory_manager = await agent.get_memory()
    if not memory_manager:
        raise ResourceNotFoundError(f"Memory not found for agent '{agent_id}'")

    user = await memory_manager.get_user(user_id)
    if not user:
        raise ResourceNotFoundError(f"User '{user_id}' not found")

    long_memory = await UserLongMemory.get_for_user(user)
    if not long_memory:
        return {"memory": {}}

    content_map = {}
    categories = await long_memory.get_all_categories()
    for cat in categories:
        if not cat.is_empty():
            content_map[cat.category] = {
                "title": cat.title,
                "content": cat.content,
                "updated_at": cat.updated_at.isoformat() if cat.updated_at else None,
            }

    return {"memory": content_map}
