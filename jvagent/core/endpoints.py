"""Agent CRUD endpoints for managing agents via RESTful API.

This module provides endpoints for:
- Getting agent details
- Updating agents (alias, enabled status, description, interaction_limit)
- Deleting agents
- Listing agents with pagination and filtering
- Graph repair (admin): runs memory repair for all agents first, then full graph repair
"""

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import Query
from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError
from jvspatial.core.pager import ObjectPager

from jvagent.core.agent import Agent
from jvagent.core.agents import Agents
from jvagent.core.graph_repair import repair_agent_graph


@endpoint(
    "/agents/{agent_id}",
    methods=["GET"],
    auth=True,
    roles=["admin"],
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
            message=f"Agent with ID '{agent_id}' not found",
            details={"agent_id": agent_id},
        )

    return {"agent": await agent.export()}


@endpoint(
    "/agents/{agent_id}",
    methods=["PUT"],
    auth=True,
    roles=["admin"],
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
            message=f"Agent with ID '{agent_id}' not found",
            details={"agent_id": agent_id},
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
    roles=["admin"],
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
            message=f"Agent with ID '{agent_id}' not found",
            details={"agent_id": agent_id},
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
    roles=["admin"],
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
    roles=["admin"],
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
            message=f"Agent with ID '{agent_id}' not found",
            details={"agent_id": agent_id},
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


@endpoint(
    "/storage/{file_path:path}",
    methods=["GET"],
    auth=False,
    tags=["Storage"],
)
async def get_storage_file(file_path: str):
    """Serve a file from the application's storage.

    No authentication required. Public access for images and media assets.

    Args:
        file_path: Relative path to the file in storage

    Returns:
        FastAPI Response with file content and correct MIME type
    """
    import mimetypes
    import os

    from fastapi import Response

    from jvagent.core.app import App

    app = await App.get()
    if not app:
        raise ResourceNotFoundError("Application not found")

    # Security: Prevent path traversal
    if ".." in file_path or file_path.startswith("/"):
        raise ValidationError("Invalid file path")

    # Get file content
    content = await app.get_file(file_path)
    if content is None:
        raise ResourceNotFoundError(f"File not found: {file_path}")

    # Determine MIME type
    mime_type, _ = mimetypes.guess_type(file_path)
    if mime_type is None:
        # Default to octet-stream for unknown types
        mime_type = "application/octet-stream"

    # Extract filename for Content-Disposition
    filename = os.path.basename(file_path)

    return Response(
        content=content,
        media_type=mime_type,
        headers={
            "Content-Disposition": f"inline; filename={filename}",
            "Cache-Control": "public, max-age=3600",
        },
    )


@endpoint(
    "/graph/repair",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["App"],
    response=success_response(
        data={
            "memory_repair_agents": ResponseField(
                field_type=int,
                description="Number of agents whose memory was repaired",
                example=2,
            ),
            "orphaned_interactions_deleted": ResponseField(
                field_type=int,
                description="Number of orphaned interactions deleted across all agents",
                example=3,
            ),
            "orphaned_users_reconnected": ResponseField(
                field_type=int,
                description="Number of orphaned users reconnected across all agents",
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
            "dead_edges_removed": ResponseField(
                field_type=int,
                description="Number of dead edges removed",
                example=2,
            ),
            "orphaned_nodes_reattached": ResponseField(
                field_type=int,
                description="Number of orphaned nodes reattached",
                example=1,
            ),
            "orphaned_nodes_deleted": ResponseField(
                field_type=int,
                description="Number of orphaned nodes deleted",
                example=0,
            ),
            "node_edge_ids_synced": ResponseField(
                field_type=int,
                description="Number of nodes with edge_ids synced",
                example=3,
            ),
            "duplicate_edges_removed": ResponseField(
                field_type=int,
                description="Number of duplicate edges removed",
                example=0,
            ),
            "message": ResponseField(
                field_type=str,
                description="Success message",
                example="Repair completed: memory repaired for 2 agent(s), 2 dead edge(s) removed",
            ),
        }
    ),
)
async def repair_graph(
    dry_run: bool = Query(
        False,
        description="If True, report issues without making changes",
    ),
    recent_minutes: Optional[int] = Query(
        None,
        description="Only clean orphan interactions from last N minutes (None = all)",
    ),
) -> Dict[str, Any]:
    """Run memory repair (all agents) then agent graph repair (admin only, manually triggered).

    Memory repair executes first for all agents, then validates graph structure,
    removes dead edges, syncs node edge_ids, reattaches or removes orphaned nodes,
    and removes duplicate edges.

    Args:
        dry_run: Optional - report issues without making changes (memory repair skipped)
        recent_minutes: Optional - passed to memory repair to limit orphan interaction cleanup

    Returns:
        Dictionary with memory repair counts and graph repair counts
    """
    result = await repair_agent_graph(
        dry_run=dry_run,
        recent_minutes=recent_minutes,
    )
    return result
