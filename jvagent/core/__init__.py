"""jvagent core entities."""

from jvagent.core import endpoints  # noqa: F401 - Import to register endpoints
from jvagent.core.agent import Agent
from jvagent.core.agents import Agents
from jvagent.core.app import App
from jvagent.core.app_loader import AppDescriptor, AppLoader
from jvagent.core.graph_traversal import traverse_to_agent, traverse_to_node_type
from jvagent.core.observability import (
    ObservabilityHook,
    clear_observability_hooks,
    emit_observability_event,
    register_observability_hook,
)
from jvagent.memory import (  # noqa: F401 - Memory admin endpoints
    endpoints as memory_endpoints,
)


def __getattr__(name: str):
    # Lazy-import AgentLoader/AgentDescriptor to avoid circular imports
    if name in ("AgentLoader", "AgentDescriptor"):
        from jvagent.core.agent_loader import AgentDescriptor, AgentLoader

        return AgentLoader if name == "AgentLoader" else AgentDescriptor
    raise AttributeError(name)


__all__ = [
    "App",
    "Agent",
    "Agents",
    "AgentLoader",
    "AgentDescriptor",
    "AppLoader",
    "AppDescriptor",
    "traverse_to_agent",
    "traverse_to_node_type",
    "ObservabilityHook",
    "register_observability_hook",
    "clear_observability_hooks",
    "emit_observability_event",
]
