"""Agent CRUD endpoints for managing agents via RESTful API.

This module provides endpoints for:
- Getting agent details
- Updating agents (alias, enabled status, description, interaction_limit)
- Deleting agents
- Listing agents with pagination and filtering
"""

import asyncio
from typing import Any, Dict, List, Optional

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError
from jvspatial.core.pager import ObjectPager

from jvagent.core.agent import Agent
from jvagent.core.agents import Agents


@endpoint(
    "/agents/{agent_id}",
    methods=["GET"],
    auth=True,
    tags=["Agent"],
    response=success_response(
        data={
            "agent": ResponseField(
                field_type=Dict[str, Any],
                description="Agent information",
                example={
                    "id": "agent_123",
                    "namespace": "jvagent",
                    "name": "my_agent",
                    "alias": "My Agent",
                    "enabled": True,
                    "description": "Agent description",
                    "interaction_limit": 100,
                },
            )
        }
    ),
)
async def get_agent(agent_id: str) -> Dict[str, Any]:
    """Get a specific agent by ID.

    Retrieves full agent information including:


    - **Identity**: namespace, name, alias
    - **Status**: enabled/disabled
    - **Description and metadata**


    The agent ID follows the format: `n.Agent.{unique_id}`


    **Args:**

    - agent_id: ID of the agent to retrieve


    **Returns:**

    Dictionary with complete agent information


    **Raises:**

    - ResourceNotFoundError: If agent not found
    """
    # Get the agent
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID '{agent_id}' not found", details={"agent_id": agent_id}
        )

    return {"agent": await agent.export()}


@endpoint(
    "/agents/{agent_id}",
    methods=["PUT"],
    auth=True,
    tags=["Agent"],
    response=success_response(
        data={
            "agent": ResponseField(
                field_type=Dict[str, Any],
                description="Updated agent information",
                example={
                    "id": "agent_123",
                    "namespace": "jvagent",
                    "name": "my_agent",
                    "alias": "Updated Agent Display Name",
                    "enabled": True,
                    "description": "Updated description",
                    "interaction_limit": 100,
                },
            ),
            "message": ResponseField(
                field_type=str,
                description="Success message",
                example="Agent updated successfully",
            ),
        }
    ),
)
async def update_agent(
    agent_id: str,
    alias: Optional[str] = None,
    enabled: Optional[bool] = None,
    description: Optional[str] = None,
    interaction_limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Update an existing Agent node.


    **Updatable Fields:**

    - **alias**: Display name shown in UI (name is static)
    - **enabled**: Enable/disable the agent
    - **description**: Agent description text
    - **interaction_limit**: Default interaction limit for conversations (0 = disabled)


    **Important Notes:**

    - The `name` field is static and cannot be changed after creation
    - Use `alias` to update the display name
    - Enabling/disabling updates the Agents manager counters


    **Args:**

    - agent_id: ID of the agent to update
    - alias: New display name (alias) for the agent
    - enabled: Whether the agent should be enabled
    - description: New description for the agent
    - interaction_limit: Default interaction limit for conversations (0 = disabled)


    **Returns:**

    Dictionary with:

    - **agent**: Updated agent information
    - **message**: Success confirmation


    **Raises:**

    - ResourceNotFoundError: If agent not found
    """
    # Get the agent
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID '{agent_id}' not found", details={"agent_id": agent_id}
        )

    # Note: 'name' is static and cannot be changed after creation
    # Use 'alias' to update the display name instead

    # Update alias if provided
    if alias is not None:
        agent.alias = alias.strip()

    # Update enabled if provided
    if enabled is not None:
        previous_enabled = agent.enabled
        agent.enabled = enabled

        # Update Agents node counters if enabled status changed
        if previous_enabled != enabled:
            # Find the Agents node connected to this agent
            connected_nodes = await agent.nodes()
            agents_nodes = [n for n in connected_nodes if isinstance(n, Agents)]
            if agents_nodes:
                agents_node = agents_nodes[0]
                if previous_enabled and not enabled:
                    agents_node.active_agents = max(0, agents_node.active_agents - 1)
                elif not previous_enabled and enabled:
                    agents_node.active_agents += 1
                await agents_node.save()

    # Update description if provided
    if description is not None:
        agent.description = description

    # Update interaction_limit if provided
    if interaction_limit is not None:
        agent.interaction_limit = interaction_limit

    # Save the updated agent
    await agent.save()

    return {"agent": await agent.export(), "message": "Agent updated successfully"}


@endpoint(
    "/agents/{agent_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Agent"],
    response=success_response(
        data={
            "message": ResponseField(
                field_type=str,
                description="Success message",
                example="Agent deleted successfully",
            ),
        }
    ),
)
async def delete_agent(agent_id: str) -> Dict[str, Any]:
    """Delete an Agent node.

    This operation cascades to delete:


    - All connected Actions
    - Memory node and all User/Conversation/Interaction data
    - Any file storage associated with the agent


    **Warning:**

    This operation is irreversible. All agent data will be permanently deleted.


    **Args:**

    - agent_id: ID of the agent to delete


    **Returns:**

    Dictionary with success message


    **Raises:**

    - ResourceNotFoundError: If agent not found
    """
    # Get the agent
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID '{agent_id}' not found", details={"agent_id": agent_id}
        )

    # Get agent enabled status before deletion for counter update
    was_enabled = agent.enabled

    # Find the Agents node connected to this agent
    connected_nodes = await agent.nodes()
    agents_nodes = [n for n in connected_nodes if isinstance(n, Agents)]

    # Delete the agent (this will also remove edges and cascade to dependent nodes)
    await agent.delete(cascade=True)

    # Update Agents node counters
    if agents_nodes:
        agents_node = agents_nodes[0]
        agents_node.total_agents = max(0, agents_node.total_agents - 1)
        if was_enabled:
            agents_node.active_agents = max(0, agents_node.active_agents - 1)
        await agents_node.save()

    return {"message": "Agent deleted successfully"}


@endpoint(
    "/agents",
    methods=["GET"],
    auth=True,
    tags=["Agent"],
    response=success_response(
        data={
            "agents": ResponseField(
                field_type=List[Dict[str, Any]],
                description="List of agents",
                example=[
                    {
                        "id": "agent_123",
                        "name": "my_agent",
                        "enabled": True,
                        "description": "Agent description",
                    }
                ],
            ),
            "total": ResponseField(
                field_type=int,
                description="Total number of agents",
                example=100,
            ),
            "page": ResponseField(
                field_type=int,
                description="Current page number",
                example=1,
            ),
            "per_page": ResponseField(
                field_type=int,
                description="Number of agents per page",
                example=10,
            ),
            "total_pages": ResponseField(
                field_type=int,
                description="Total number of pages",
                example=10,
            ),
            "has_previous": ResponseField(
                field_type=bool,
                description="Whether there's a previous page",
                example=False,
            ),
            "has_next": ResponseField(
                field_type=bool,
                description="Whether there's a next page",
                example=True,
            ),
            "previous_page": ResponseField(
                field_type=Optional[int],  # type: ignore[arg-type]
                description="Previous page number",
                example=None,
            ),
            "next_page": ResponseField(
                field_type=Optional[int],  # type: ignore[arg-type]
                description="Next page number",
                example=2,
            ),
        }
    ),
)
async def list_agents(
    page: int = 1,
    per_page: int = 10,
    enabled: Optional[bool] = None,
    search: Optional[str] = None,
) -> Dict[str, Any]:
    """List all agents with pagination and optional filtering.

    Supports flexible querying:


    - **Pagination**: Control page size and navigate through results
    - **Status filtering**: Show only enabled or disabled agents
    - **Text search**: Find agents by name, alias, or description


    The response includes full pagination metadata for building
    navigation controls in client applications.


    **Args:**

    - page: Page number (default: 1)
    - per_page: Number of agents per page (default: 10)
    - enabled: Filter by enabled status (optional)
    - search: Search by name, alias, or description (optional)


    **Returns:**

    Dictionary containing:

    - **agents**: List of agent objects
    - **total**: Total number of matching agents
    - **page**: Current page number
    - **per_page**: Items per page
    - **total_pages**: Total page count
    - **has_previous/has_next**: Navigation indicators
    - **previous_page/next_page**: Adjacent page numbers
    """
    # Build filters for pagination
    filters = {}
    if enabled is not None:
        filters["context.enabled"] = enabled

    # Create pager with filters
    pager = ObjectPager(Agent, page_size=per_page, filters=filters)

    # Get the requested page
    agents: List[Agent] = await pager.get_page(page=page)

    # Apply text search if provided (post-filter on results)
    if search:
        search_lower = search.lower()
        agents = [
            a
            for a in agents
            if search_lower in a.name.lower()
            or search_lower in (a.alias.lower() if a.alias else "")
            or search_lower in a.description.lower()
        ]

    # Convert to dictionaries using export
    agents_list = await asyncio.gather(*[agent.export() for agent in agents])

    # Get pagination info from pager
    pagination_info = pager.to_dict()

    return {
        "agents": agents_list,
        "total": pagination_info["total_items"],
        "page": pagination_info["current_page"],
        "per_page": pagination_info["page_size"],
        "total_pages": pagination_info["total_pages"],
        "has_previous": pagination_info["has_previous"],
        "has_next": pagination_info["has_next"],
        "previous_page": pagination_info["previous_page"],
        "next_page": pagination_info["next_page"],
    }


@endpoint(
    "/agents/{agent_id}/conversations/{user_id}/{session_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Agent"],
    response=success_response(
        data={
            "message": ResponseField(
                field_type=str,
                description="Success message",
                example="Conversation deleted successfully",
            ),
        }
    ),
)
async def delete_conversation(
    agent_id: str,
    user_id: str,
    session_id: str,
) -> Dict[str, Any]:
    """Delete a conversation and all its related interactions via cascade delete.

    This endpoint deletes a conversation identified by session_id and validates
    that it belongs to the specified user_id before deletion. The deletion
    automatically cascades to remove all related Interaction nodes.

    **Cascade Delete Behavior:**
    - Deletes the Conversation node
    - Automatically deletes all connected Interaction nodes
    - Updates Memory's total_conversations counter

    **Args:**
    - agent_id: ID of the agent that owns the conversation
    - user_id: User identifier (required for ownership validation)
    - session_id: Session identifier for the conversation to delete

    **Returns:**
    Dictionary with success message

    **Raises:**
    - ResourceNotFoundError: If agent or conversation not found
    - ValidationError: If conversation doesn't belong to the specified user_id
    """
    # Get the agent
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID '{agent_id}' not found", details={"agent_id": agent_id}
        )

    # Get Memory node from agent
    memory = await agent.get_memory()
    if not memory:
        raise ResourceNotFoundError(
            message=f"Memory node not found for agent '{agent_id}'",
            details={"agent_id": agent_id},
        )

    # Find conversation by session_id
    conversation = await memory.get_conversation_by_session(session_id)
    if not conversation:
        raise ResourceNotFoundError(
            message=f"Conversation with session_id '{session_id}' not found",
            details={"session_id": session_id, "agent_id": agent_id},
        )

    # Validate that the conversation belongs to the specified user_id
    if conversation.user_id != user_id:
        raise ValidationError(
            message=f"Conversation with session_id '{session_id}' does not belong to user '{user_id}'",
            details={
                "session_id": session_id,
                "user_id": user_id,
                "conversation_user_id": conversation.user_id,
            },
        )

    # Delete the conversation with cascade=True
    # This will automatically:
    # - Delete all connected Interaction nodes
    # - Update Memory's total_conversations counter (via Conversation.delete override)
    # - Remove all edges
    await conversation.delete(cascade=True)

    return {"message": "Conversation deleted successfully"}

