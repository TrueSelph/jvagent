"""ActionResolver: lightweight helper for skill tools to access graph-persisted Actions.

Attached to ``visitor.action_resolver`` by SkillInteractAction during
execute(). Skill tools that accept a ``visitor`` kwarg can access it via
``visitor.action_resolver.resolve("GoogleCalendarAction")``.

Actions are cached per-interaction so that repeated calls within the same
agentic loop do not re-query the graph.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ActionResolver:
    """Resolve graph-persisted Actions by entity type for skill tool modules.

    Args:
        agent: The Agent node (``visitor._agent``), used to call
            ``get_action_by_type()`` for resolution.
    """

    def __init__(self, agent: Any) -> None:
        self._agent = agent
        self._cache: Dict[str, Optional[Any]] = {}

    async def resolve(self, entity_type: str) -> Optional[Any]:
        """Return the first Action matching *entity_type*, or None.

        Results are cached per entity_type for the lifetime of the resolver
        (i.e. the current interaction).

        Args:
            entity_type: Action class name (e.g. ``"GoogleCalendarAction"``).

        Returns:
            The resolved Action instance, or None if not found.
        """
        if entity_type in self._cache:
            return self._cache[entity_type]

        action = await self._agent.get_action_by_type(entity_type)
        self._cache[entity_type] = action
        return action

    async def require(self, entity_type: str) -> Any:
        """Return the Action matching *entity_type*, raising if absent/disabled.

        Used internally by activation validation; skill tools should prefer
        ``resolve()`` for graceful degradation.

        Args:
            entity_type: Action class name.

        Returns:
            The resolved Action instance.

        Raises:
            ValueError: If the action is not found or is disabled.
        """
        action = await self.resolve(entity_type)
        if action is None:
            raise ValueError(f"Required action '{entity_type}' not found on this agent")
        if getattr(action, "enabled", True) is False:
            raise ValueError(f"Required action '{entity_type}' exists but is disabled")
        return action

    async def validate_requirements(self, required_types: List[str]) -> List[str]:
        """Validate all required action types are present and enabled.

        Args:
            required_types: List of Action class names to validate.

        Returns:
            List of error messages (empty if all valid).
        """
        errors: List[str] = []
        for entity_type in required_types:
            try:
                await self.require(entity_type)
            except ValueError as exc:
                errors.append(str(exc))
        return errors
