"""Agent interact action package.

Provides the unified AgentInteractAction that replaces the legacy
InteractRouter + SkillInteractAction pair with a single walker visit.
"""

from jvagent.action.agent_interact.agent_interact_action import AgentInteractAction

__all__ = ["AgentInteractAction"]
