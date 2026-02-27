"""Agent node and CRUD operations."""

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.core import Node
from jvspatial.core.annotations import attribute

if TYPE_CHECKING:
    from jvagent.action.actions import Actions
    from jvagent.action.response.response_bus import ResponseBus

logger = logging.getLogger(__name__)


class Agent(Node):
    """Individual agent node in the system.

    Attributes:
        namespace: Namespace for the agent (e.g., 'jvagent', 'contrib')
        name: Unique machine name for the agent within the namespace (required, static)
        alias: Human-readable display name for the agent (optional)
        enabled: Whether the agent is enabled (default: True)
        description: Optional description of the agent
    """

    namespace: str = attribute(indexed=True, description="Namespace for the agent")
    name: str = attribute(
        indexed=True, index_unique=True, description="Unique machine name for the agent"
    )
    alias: str = attribute(description="Human-readable display name")
    enabled: bool = attribute(default=True, description="Whether the agent is enabled")
    description: str = attribute(description="Optional description of the agent")
    interaction_limit: int = attribute(
        default=0,
        description="Default interaction limit for conversations (0 = disabled, no pruning). Can be overridden per conversation.",
    )
    max_statement_length: Optional[int] = attribute(
        default=None,
        description="Default maximum length for truncating utterances and responses in conversation history. Can be overridden when calling methods that accept max_statement_length parameter. None = no truncation.",
    )

    # Runtime instances (private, transient)
    _response_bus: Any = attribute(private=True, default=None)

    # =========================================================================
    # Graph Navigation Helpers
    # =========================================================================

    async def get_actions_manager(self) -> Optional["Actions"]:
        """Get the Actions manager node for this agent.

        Returns:
            Actions manager node if found, None otherwise
        """
        return await self.node(node="Actions")

    async def get_action(self, action_label: str) -> Optional[Any]:
        """Get an action by its label.

        Args:
            action_label: The label of the action to retrieve

        Returns:
            Action instance if found, None otherwise
        """
        actions_manager = await self.get_actions_manager()
        if not actions_manager:
            return None
        return await actions_manager.get_action_by_label(action_label)

    async def get_action_by_type(self, entity_type: str) -> Optional[Any]:
        """Get the first action matching the given entity type.

        This is useful for finding actions like "OpenAILanguageModelAction" without
        needing to know the specific ID or label.

        Args:
            entity_type: Entity type name (e.g., "OpenAILanguageModelAction", "PersonaAction")

        Returns:
            Action instance if found, None otherwise
        """
        from jvagent.action.base import Action

        # Use entity-centric find_one with explicit entity filter
        # This queries for the specific entity type belonging to this agent
        return await Action.find_one(
            {
                "entity": entity_type,
                "context.agent_id": self.id,
            }
        )

    async def get_actions(self, enabled_only: bool = False) -> List[Any]:
        """Get all actions for this agent.

        Args:
            enabled_only: If True, only return enabled actions

        Returns:
            List of Action instances
        """
        actions_manager = await self.get_actions_manager()
        if not actions_manager:
            return []
        return await actions_manager.get_actions(enabled_only=enabled_only)

    async def get_memory(self) -> Optional[Any]:
        """Get the Memory node for this agent.

        Returns:
            Memory node if found, None otherwise
        """
        return await self.node(node="Memory")

    # =========================================================================
    # Response Bus (Agent-Scoped)
    # =========================================================================

    async def get_response_bus(self) -> "ResponseBus":
        """Get or initialize the agent-scoped ResponseBus instance.

        Each agent owns exactly one ResponseBus instance. Channel adapters and
        filters from this agent's actions register with this bus.

        Returns:
            ResponseBus instance for this agent
        """
        if self._response_bus is None:
            from jvagent.action.response.response_bus import ResponseBus

            self._response_bus = ResponseBus()
        return self._response_bus

    async def save(self, *args, **kwargs):
        """Save the agent and invalidate cache.

        Overrides Node.save() to invalidate the agent cache after saving,
        ensuring cached agents reflect the latest state.

        Args:
            *args: Positional arguments passed to parent save()
            **kwargs: Keyword arguments passed to parent save()

        Returns:
            Result from parent save()

        Note:
            Cache invalidation errors are logged but do not prevent the save
            from succeeding. This ensures data is persisted even if cache
            operations fail.
        """
        result = await super().save(*args, **kwargs)
        # Invalidate cache after save to ensure consistency
        try:
            from jvagent.core.cache import invalidate_agent_cache

            await invalidate_agent_cache(self.id)
        except Exception as e:
            # Log but don't fail - save already succeeded
            logger.warning(f"Failed to invalidate agent cache for {self.id}: {e}")
        return result
