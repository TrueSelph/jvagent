"""Agent node and CRUD operations."""

from typing import Any, Dict, List, Optional

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceConflictError, ResourceNotFoundError
from jvspatial.core import Node, Root, Walker, on_visit
from jvspatial.core.pager import ObjectPager

from jvagent.core.agents import Agents
from jvagent.core.app import App


class Agent(Node):
    """Individual agent node in the system.
    
    Attributes:
        name: Unique name for the agent (required)
        status: Agent status (e.g., "active", "inactive", "paused")
        description: Optional description of the agent
    """
    name: str = ""
    status: str = "active"  # active, inactive, paused
    description: str = ""


# =============================================================================
# WALKER: Create Agent
# =============================================================================

@endpoint(
    "/agents",
    methods=["POST"],
    auth=True,
    tags=["Agent"],
    response=success_response(
        data={
            "agent": ResponseField(
                field_type=Dict[str, Any],
                description="Created agent information",
                example={
                    "id": "agent_123",
                    "name": "my_agent",
                    "status": "active",
                    "description": "My agent description",
                },
            ),
            "message": ResponseField(
                field_type=str,
                description="Success message",
                example="Agent created successfully",
            ),
        }
    ),
)
class CreateAgent(Walker):
    """Walker to create a new Agent node and attach it to the Agents node.
    
    Traverses: Root -> App -> Agents
    Creates Agent node with unique name validation
    Connects Agent to Agents node with bidirectional edge
    """
    
    name: str
    status: str = "active"
    description: str = ""
    
    @on_visit(Root)
    async def visit_root(self, here: Root) -> None:
        """Start traversal from Root to connected App nodes."""
        connected_nodes = await here.nodes()
        app_nodes = [n for n in connected_nodes if isinstance(n, App)]
        if app_nodes:
            await self.visit(app_nodes)
        else:
            await self.report({
                "error": "App node not found. Please bootstrap the application first."
            })
    
    @on_visit(App)
    async def visit_app(self, here: App) -> None:
        """Continue traversal from App to connected Agents node."""
        connected_nodes = await here.nodes()
        agents_nodes = [n for n in connected_nodes if isinstance(n, Agents)]
        if agents_nodes:
            await self.visit(agents_nodes)
        else:
            await self.report({
                "error": "Agents node not found. Please bootstrap the application first."
            })
    
    @on_visit(Agents)
    async def create_and_attach_agent(self, here: Agents) -> None:
        """Create Agent node and attach it to Agents node with bidirectional edge."""
        # Validate name is provided
        if not self.name or not self.name.strip():
            await self.report({
                "error": "Agent name is required"
            })
            return
        
        name = self.name.strip()
        
        # Check for uniqueness - find existing agents with the same name
        existing_agents = await Agent.find({"context.name": name})
        if existing_agents:
            await self.report({
                "error": f"Agent with name '{name}' already exists",
                "conflict": True
            })
            return
        
        # Create the Agent node
        try:
            agent = await Agent.create(
                name=name,
                status=self.status,
                description=self.description
            )
            
            # Connect Agent to Agents node with bidirectional edge
            # Using direction="both" ensures the edge is bidirectional, allowing
            # traversal from Agents -> Agent and Agent -> Agents
            edge = await here.connect(agent, direction="both")
            
            # Verify the edge is bidirectional
            if not edge.bidirectional:
                await self.report({
                    "error": "Failed to create bidirectional edge",
                    "warning": "Edge created but may not be bidirectional"
                })
                return
            
            # Update Agents node counters
            here.total_agents += 1
            if self.status == "active":
                here.active_agents += 1
            await here.save()
            
            # Report success with edge information
            await self.report({
                "agent": agent.export(),
                "edge_id": edge.id,
                "edge_bidirectional": edge.bidirectional,
                "message": "Agent created successfully and connected with bidirectional edge"
            })
            
        except Exception as e:
            await self.report({
                "error": f"Failed to create agent: {str(e)}"
            })


# =============================================================================
# ENDPOINTS: Get, Update, Delete, List Agents
# =============================================================================

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
                    "name": "my_agent",
                    "status": "active",
                    "description": "Agent description",
                },
            )
        }
    ),
)
async def get_agent(agent_id: str) -> Dict[str, Any]:
    """Get a specific agent by ID.
    
    Args:
        agent_id: ID of the agent to retrieve
    
    Returns:
        Dictionary with agent information
    
    Raises:
        ResourceNotFoundError: If agent not found
    """
    # Get the agent
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID '{agent_id}' not found",
            details={"agent_id": agent_id}
        )
    
    return {
        "agent": agent.export()
    }


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
                    "name": "updated_agent",
                    "status": "active",
                    "description": "Updated description",
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
    name: Optional[str] = None,
    status: Optional[str] = None,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """Update an existing Agent node.
    
    Args:
        agent_id: ID of the agent to update
        name: New name for the agent (must be unique if provided)
        status: New status for the agent
        description: New description for the agent
    
    Returns:
        Dictionary with updated agent information and success message
    
    Raises:
        ResourceNotFoundError: If agent not found
        ResourceConflictError: If new name conflicts with existing agent
    """
    # Get the agent
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID '{agent_id}' not found",
            details={"agent_id": agent_id}
        )
    
    # Check name uniqueness if name is being changed
    if name is not None and name.strip() != agent.name:
        new_name = name.strip()
        existing_agents = await Agent.find({"context.name": new_name})
        if existing_agents and existing_agents[0].id != agent_id:
            raise ResourceConflictError(
                message=f"Agent with name '{new_name}' already exists",
                details={"name": new_name, "agent_id": agent_id}
            )
        agent.name = new_name
    
    # Update status if provided
    if status is not None:
        old_status = agent.status
        agent.status = status
        
        # Update Agents node counters if status changed
        if old_status != status:
            # Find the Agents node connected to this agent
            connected_nodes = await agent.nodes()
            agents_nodes = [n for n in connected_nodes if isinstance(n, Agents)]
            if agents_nodes:
                agents_node = agents_nodes[0]
                if old_status == "active" and status != "active":
                    agents_node.active_agents = max(0, agents_node.active_agents - 1)
                elif old_status != "active" and status == "active":
                    agents_node.active_agents += 1
                await agents_node.save()
    
    # Update description if provided
    if description is not None:
        agent.description = description
    
    # Save the updated agent
    await agent.save()
    
    return {
        "agent": agent.export(),
        "message": "Agent updated successfully"
    }


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
    
    Args:
        agent_id: ID of the agent to delete
    
    Returns:
        Dictionary with success message
    
    Raises:
        ResourceNotFoundError: If agent not found
    """
    # Get the agent
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID '{agent_id}' not found",
            details={"agent_id": agent_id}
        )
    
    # Get agent status before deletion for counter update
    was_active = agent.status == "active"
    
    # Find the Agents node connected to this agent
    connected_nodes = await agent.nodes()
    agents_nodes = [n for n in connected_nodes if isinstance(n, Agents)]
    
    # Delete the agent (this will also remove edges and cascade to dependent nodes)
    await agent.delete()
    
    # Update Agents node counters
    if agents_nodes:
        agents_node = agents_nodes[0]
        agents_node.total_agents = max(0, agents_node.total_agents - 1)
        if was_active:
            agents_node.active_agents = max(0, agents_node.active_agents - 1)
        await agents_node.save()
    
    return {
        "message": "Agent deleted successfully"
    }


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
                        "status": "active",
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
    status: Optional[str] = None,
    search: Optional[str] = None,
) -> Dict[str, Any]:
    """List all agents with pagination and optional filtering.
    
    Args:
        page: Page number (default: 1)
        per_page: Number of agents per page (default: 10)
        status: Filter by status (optional)
        search: Search by name or description (optional)
    
    Returns:
        Dictionary with paginated list of agents and pagination metadata
    """
    # Build filters for pagination
    filters = {}
    if status:
        filters["context.status"] = status
    
    # Create pager with filters
    pager = ObjectPager(Agent, page_size=per_page, filters=filters)
    
    # Get the requested page
    agents: List[Agent] = await pager.get_page(page=page)
    
    # Apply text search if provided (post-filter on results)
    if search:
        search_lower = search.lower()
        agents = [
            a for a in agents
            if search_lower in a.name.lower() or search_lower in a.description.lower()
        ]
    
    # Convert to dictionaries using export
    agents_list = [a.export() for a in agents]
    
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
