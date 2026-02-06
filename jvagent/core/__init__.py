"""jvagent core entities."""

from jvagent.core.agent import Agent
from jvagent.core.agents import Agents
from jvagent.core.app import App
from jvagent.core.app_loader import AppDescriptor, AppLoader
from jvagent.core import endpoints  # noqa: F401 - Import to register endpoints


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
]
