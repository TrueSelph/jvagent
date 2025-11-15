"""Agents node - Manager for all agents in the system."""

from jvspatial.core import Node


class Agents(Node):
    """Agents manager node for managing all agents in the system.
    
    This node manages the registration, discovery, and lifecycle of all
    agents in the jvagent application.
    
    Attributes:
        total_agents: Total number of agents registered
        active_agents: Number of currently active agents
    """
    total_agents: int = 0
    active_agents: int = 0
