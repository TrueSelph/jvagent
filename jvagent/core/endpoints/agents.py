"""Agent CRUD endpoints (GET / PUT / DELETE / list)."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError
from jvspatial.core.pager import ObjectPager

from jvagent.core.agent import Agent
from jvagent.core.agents import Agents


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
    """Get a specific agent by ID."""
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

    Updatable fields: ``alias``, ``enabled``, ``description``, ``interaction_limit``.
    ``name`` is static. Toggling ``enabled`` keeps the ``Agents`` counters in sync.
    """
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID '{agent_id}' not found",
            details={"agent_id": agent_id},
        )

    if alias is not None:
        agent.alias = alias.strip()

    if enabled is not None:
        previous_enabled = agent.enabled
        agent.enabled = enabled
        if previous_enabled != enabled:
            connected_nodes = await agent.nodes()
            agents_nodes = [n for n in connected_nodes if isinstance(n, Agents)]
            if agents_nodes:
                agents_node = agents_nodes[0]
                if previous_enabled and not enabled:
                    agents_node.active_agents = max(0, agents_node.active_agents - 1)
                elif not previous_enabled and enabled:
                    agents_node.active_agents += 1
                await agents_node.save()

    if description is not None:
        agent.description = description

    if interaction_limit is not None:
        agent.interaction_limit = interaction_limit

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
    """Delete an Agent node and cascade to all dependent state."""
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID '{agent_id}' not found",
            details={"agent_id": agent_id},
        )

    was_enabled = agent.enabled
    connected_nodes = await agent.nodes()
    agents_nodes = [n for n in connected_nodes if isinstance(n, Agents)]

    await agent.delete(cascade=True)

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
            ),
            "total": ResponseField(field_type=int, description="Total number of agents"),
            "page": ResponseField(field_type=int, description="Current page number"),
            "per_page": ResponseField(
                field_type=int, description="Number of agents per page"
            ),
            "total_pages": ResponseField(
                field_type=int, description="Total number of pages"
            ),
            "has_previous": ResponseField(
                field_type=bool, description="Whether there's a previous page"
            ),
            "has_next": ResponseField(
                field_type=bool, description="Whether there's a next page"
            ),
            "previous_page": ResponseField(
                field_type=Optional[int],  # type: ignore[arg-type]
                description="Previous page number",
            ),
            "next_page": ResponseField(
                field_type=Optional[int],  # type: ignore[arg-type]
                description="Next page number",
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
    """List agents with pagination plus optional ``enabled`` and ``search`` filters."""
    filters: Dict[str, Any] = {}
    if enabled is not None:
        filters["context.enabled"] = enabled

    if not search:
        pager = ObjectPager(Agent, page_size=per_page, filters=filters)
        agents: List[Agent] = await pager.get_page(page=page)
        agents_list = await asyncio.gather(*[agent.export() for agent in agents])
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

    # Search filters across the full matching set so pagination metadata
    # reflects filtered totals, not just the current page.
    search_lower = search.lower()
    all_agents: List[Agent] = await Agent.find(filters)
    filtered_agents = [
        agent
        for agent in all_agents
        if search_lower in agent.name.lower()
        or search_lower in (agent.alias.lower() if agent.alias else "")
        or search_lower in agent.description.lower()
    ]

    total_items = len(filtered_agents)
    if per_page <= 0:
        per_page = 10
    total_pages = max(1, (total_items + per_page - 1) // per_page) if total_items else 0
    page = max(1, page)
    if total_pages and page > total_pages:
        page = total_pages

    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    paged_agents = filtered_agents[start_idx:end_idx]
    agents_list = await asyncio.gather(*[agent.export() for agent in paged_agents])

    has_previous = page > 1 and total_pages > 0
    has_next = page < total_pages
    return {
        "agents": agents_list,
        "total": total_items,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "has_previous": has_previous,
        "has_next": has_next,
        "previous_page": page - 1 if has_previous else None,
        "next_page": page + 1 if has_next else None,
    }
