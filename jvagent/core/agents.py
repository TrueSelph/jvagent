"""Agents node - Structural branchpoint for agent collection and aggregation."""

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.core import Node
from jvspatial.core.annotations import attribute

from jvagent.core.app import App

if TYPE_CHECKING:
    from jvagent.core.agent import Agent


class Agents(Node):
    """Structural branchpoint node for organizing and aggregating all agents.

    The Agents node serves as a collection branchpoint in the spatial graph hierarchy,
    following the jvspatial object-spatial arrangement pattern. It provides structural
    organization and aggregation capabilities for all agents in the application.

    **Purpose:**
    - Structural organization: Groups all Agent nodes under a single branchpoint,
      creating a clear hierarchy (App → Agents → Agent)
    - Aggregation point: Maintains aggregate statistics (total_agents, active_agents)
      that are updated when agents are created, modified, or deleted
    - Traversal waypoint: Serves as a standard waypoint for walkers following the
      path Root → App → Agents → Agent

    **Note:** CRUD operations for individual agents are handled by the Agent node
    (get_agent, update_agent, delete_agent, list_agents). This node focuses solely
    on structural organization and aggregate statistics.

    Attributes:
        total_agents: Total number of agents registered in the system
        active_agents: Number of currently enabled agents (enabled=True)
    """

    total_agents: int = attribute(default=0, description="Total number of agents registered")
    active_agents: int = attribute(default=0, description="Number of currently active agents")

    # ============================================================================
    # Helper Methods
    # ============================================================================

    async def get_connected_agents(self) -> List["Agent"]:
        """Get all Agent nodes connected to this Agents node.

        Returns:
            List of Agent nodes connected to this Agents node
        """
        # Import here to avoid circular import
        from jvagent.core.agent import Agent

        connected_nodes = await self.nodes()
        return [n for n in connected_nodes if isinstance(n, Agent)]

    async def sync_counters(self) -> Dict[str, int]:
        """Recalculate and sync counters from actual agent data.

        This method queries all connected agents and updates the counters
        to match the actual state. Useful for ensuring counters stay accurate
        if agents are modified outside of standard CRUD operations.

        Returns:
            Dictionary with updated counter values:
            {
                "total_agents": int,
                "active_agents": int
            }
        """
        agents = await self.get_connected_agents()
        total = len(agents)
        active = len([a for a in agents if a.enabled])

        self.total_agents = total
        self.active_agents = active
        await self.save()

        return {"total_agents": total, "active_agents": active}

    async def get_enabled_breakdown(self) -> Dict[str, int]:
        """Get breakdown of agents by enabled status.

        Returns:
            Dictionary mapping enabled status to count:
            {
                "enabled": int,
                "disabled": int,
                "total": int
            }
        """
        agents = await self.get_connected_agents()
        breakdown = {"enabled": 0, "disabled": 0, "total": len(agents)}

        for agent in agents:
            if agent.enabled:
                breakdown["enabled"] += 1
            else:
                breakdown["disabled"] += 1

        return breakdown

    async def get_healthcheck_data(self) -> Dict[str, Any]:
        """Collect healthcheck data from all connected agents.

        Returns:
            Dictionary with healthcheck summary:
            {
                "total_agents": int,
                "healthy_agents": int,
                "unhealthy_agents": int,
                "agent_health": List[Dict[str, Any]]  # Per-agent health data
            }
        """
        agents = await self.get_connected_agents()
        agent_health = []
        healthy_count = 0
        unhealthy_count = 0

        for agent in agents:
            health_data = {
                "agent_id": agent.id,
                "namespace": agent.namespace,
                "name": agent.name,
                "alias": agent.alias,
                "description": agent.description,
                "enabled": agent.enabled,
            }

            # Try to get healthcheck if agent has the method
            try:
                if hasattr(agent, "healthcheck") and callable(getattr(agent, "healthcheck")):
                    health_result = await agent.healthcheck()
                    if isinstance(health_result, dict):
                        health_data["health"] = health_result
                        health_status = health_result.get("status", 200)
                        health_data["healthy"] = health_status == 200
                    else:
                        health_data["healthy"] = bool(health_result)
                        health_data["health"] = {"status": 200 if health_result else 500}
                else:
                    # Basic health based on enabled status
                    health_data["healthy"] = agent.enabled
                    health_data["health"] = {
                        "status": 200 if agent.enabled else 503,
                        "message": f"Agent enabled: {agent.enabled}",
                    }
            except Exception as e:
                health_data["healthy"] = False
                health_data["health"] = {"status": 500, "error": str(e)}

            if health_data.get("healthy", False):
                healthy_count += 1
            else:
                unhealthy_count += 1

            agent_health.append(health_data)

        return {
            "total_agents": len(agents),
            "healthy_agents": healthy_count,
            "unhealthy_agents": unhealthy_count,
            "agent_health": agent_health,
        }

    @classmethod
    async def get(cls) -> Optional["Agents"]:
        """Get the Agents node from the graph.

        Traverses: Root -> App -> Agents

        Returns:
            Agents node if found, None otherwise
        """
        app = await App.get()
        if not app:
            return None

        connected_nodes = await app.nodes()
        agents_nodes = [n for n in connected_nodes if isinstance(n, Agents)]

        if agents_nodes:
            return agents_nodes[0]

        return None

    async def get_statistics(
        self,
        sync: bool = False,
        include_health: bool = True,
    ) -> Dict[str, Any]:
        """Get comprehensive statistics about all agents.

        Provides aggregate statistics including counters, enabled breakdown,
        and optional healthcheck data for all agents in the system.

        Args:
            sync: If True, recalculate counters from actual agent data before returning
            include_health: If True, include healthcheck data for each agent

        Returns:
            Dictionary with comprehensive statistics:
            {
                "counters": {
                    "total_agents": int,
                    "active_agents": int
                },
                "enabled_breakdown": {
                    "enabled": int,
                    "disabled": int,
                    "total": int
                },
                "healthcheck": {
                    "total_agents": int,
                    "healthy_agents": int,
                    "unhealthy_agents": int,
                    "agent_health": List[Dict[str, Any]]
                }  # Only included if include_health=True
            }
        """
        # Sync counters if requested
        if sync:
            await self.sync_counters()

        # Get statistics
        counters = {"total_agents": self.total_agents, "active_agents": self.active_agents}

        enabled_breakdown = await self.get_enabled_breakdown()

        statistics = {"counters": counters, "enabled_breakdown": enabled_breakdown}

        # Include healthcheck data if requested
        if include_health:
            healthcheck_data = await self.get_healthcheck_data()
            statistics["healthcheck"] = healthcheck_data

        return {"statistics": statistics}


# ============================================================================
# ENDPOINT: Get Agents Status/Statistics
# ============================================================================


@endpoint(
    "/status",
    methods=["GET"],
    auth=True,  # Requires authentication - statistics contain sensitive system information
    tags=["App"],
    response=success_response(
        data={
            "statistics": ResponseField(
                field_type=Dict[str, Any],
                description="Comprehensive statistics about all agents",
                example={
                    "counters": {"total_agents": 10, "active_agents": 8},
                    "enabled_breakdown": {"enabled": 8, "disabled": 2, "total": 10},
                    "healthcheck": {
                        "total_agents": 10,
                        "healthy_agents": 9,
                        "unhealthy_agents": 1,
                        "agent_health": [],
                    },
                },
            )
        }
    ),
)
async def get_status(
    sync: bool = False,
    include_health: bool = True,
) -> Dict[str, Any]:
    """Get comprehensive statistics about all agents.

    Provides aggregate statistics including counters, enabled breakdown,
    and optional healthcheck data for all agents in the system.


    This endpoint requires authentication as it exposes sensitive system
    information including agent counts, status breakdowns, and health data.


    **Args:**

    - sync: If True, recalculate counters from actual agent data before returning
    - include_health: If True, include healthcheck data for each agent


    **Returns:**

    Dictionary with comprehensive statistics:

    ```json
    {
        "statistics": {
            "counters": {
                "total_agents": 10,
                "active_agents": 8
            },
            "enabled_breakdown": {
                "enabled": 8,
                "disabled": 2,
                "total": 10
            },
            "healthcheck": {
                "total_agents": 10,
                "healthy_agents": 9,
                "unhealthy_agents": 1,
                "agent_health": []
            }
        }
    }
    ```

    Note: The `healthcheck` field is only included if `include_health=True`.
    """
    # Get Agents node
    agents_node = await Agents.get()
    if not agents_node:
        return {
            "statistics": {
                "error": "Agents node not found. Please bootstrap the application first."
            }
        }

    # Get statistics from the Agents node
    return await agents_node.get_statistics(sync=sync, include_health=include_health)
