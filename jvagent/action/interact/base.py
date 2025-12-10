"""InteractAction base class for pluggable interact subsystem.

This module provides the InteractAction base class that extends Action and
defines the interface for actions that participate in the interact subsystem.
"""

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.core import on_visit
from jvspatial.core.annotations import attribute

from jvagent.action.base import Action

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker

# Import InteractWalker for @on_visit decorator (needed at class definition time)
# This import is safe because InteractWalker only imports InteractAction for type hints
try:
    from jvagent.action.interact.interact_walker import InteractWalker
except ImportError:
    # If import fails, we'll use string matching in walker
    InteractWalker = None  # type: ignore


class InteractAction(Action, ABC):
    """Base class for actions that participate in the interact subsystem.

    InteractAction extends Action and provides the interface for actions that
    are traversed by InteractWalker. These actions implement execute() which
    is automatically called by the walker when the action is visited.

    The execute() method is automatically invoked by InteractWalker when it visits
    an InteractAction node. The walker performs routing checks first (if InteractRouter
    has executed), then automatically calls execute() if the action should run.

    Implementations should perform evaluation checks at the start of execute()
    and return early if conditions aren't met. This allows flexible, custom
    evaluation logic while keeping the API simple.

    Attributes:
        weight: Execution precedence for top-tier InteractActions only (lower = earlier,
            negative allowed for higher precedence). Weight is only considered when
            InteractActions are launched from the Actions node (top tier). Sub-actions
            (InteractActions connected to other InteractActions) are traversed in
            graph-based arrangement without weight consideration.
    """

    # Weight attribute for execution ordering (top tier only)
    weight: int = attribute(
        default=0,
        description=(
            "Execution precedence for top-tier InteractActions only "
            "(lower = earlier, negative allowed for higher precedence). "
            "Only applied when launching from Actions node. Sub-actions are "
            "traversed in graph-based arrangement without weight consideration."
        ),
    )
    description: str = attribute(
        default_factory=str,
        description="Action description"
    )

    # Anchors for routing (published by InteractRouter)
    anchors: List[str] = attribute(
        default_factory=list,
        description=(
            "Anchor statements for routing. List of statements describing when this action should be used. "
            "The action's class/entity name is automatically used as the key when collected by InteractRouter."
        ),
    )

    @abstractmethod
    async def execute(self, visitor: "InteractWalker") -> None:
        """Execute the action's logic on the interaction.

        This method is conditionally called by InteractWalker when it visits this
        InteractAction node. The walker handles routing checks and conditional
        execution before invoking this method.

        Implementations should perform evaluation checks at the start and return
        early if conditions aren't met. This allows flexible, custom evaluation
        logic while keeping the API simple.

        Example:
            async def execute(self, visitor: "InteractWalker") -> None:
                # Evaluation checks at the start
                if not self._should_run(visitor):
                    return  # Early return if conditions not met

                # Execution logic here
                interaction = visitor.interaction
                # ... perform action logic ...

        Args:
            visitor: The InteractWalker visiting this action

        Note:
            - This method is conditionally invoked by the walker - no @on_visit decorator needed
            - Access the Interaction via visitor.interaction
            - Access action properties via self (the node instance)
            - The walker performs routing checks before calling execute()
        """
        pass

    async def _get_persona_action(self) -> Optional[Any]:
        """Get the PersonaAction for responding with persona prompt.

        Returns:
            PersonaAction instance or None if not found
        """
        agent = await self.get_agent()
        if not agent:
            logger.error("InteractAction: Agent not found")
            return None

        from jvagent.action.persona.base import PersonaAction

        actions_manager = await agent.get_actions_manager()
        if actions_manager:
            all_actions = await actions_manager.get_actions(enabled_only=True)
            for action in all_actions:
                if isinstance(action, PersonaAction):
                    return action
        return None

    async def publish_response(
        self,
        visitor: "InteractWalker",
        content: str,
        message_type: str = "adhoc",
        channel: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Publish a response message to the response bus.

        This helper method allows InteractActions to easily publish adhoc responses
        or stream chunks to the response bus.

        Args:
            visitor: The InteractWalker visiting this action
            content: Message content to publish
            message_type: Type of message ("adhoc", "stream_chunk", "final")
            channel: Target channel (defaults to visitor.channel)
            metadata: Additional metadata

        Returns:
            Created ResponseMessage object (non-persisted)

        Example:
            async def execute(self, visitor: "InteractWalker") -> None:
                # Publish an adhoc response
                await self.publish_response(
                    visitor,
                    "Processing your request...",
                    message_type="adhoc"
                )

                # Continue with main logic
                # ...
        """
        if not visitor.response_bus:
            logger.warning(
                "ResponseBus not available - cannot publish response. "
                "Ensure InteractWalker has response_bus initialized."
            )
            return None

        if not visitor.session_id:
            logger.warning(
                "Session ID not available - cannot publish response"
            )
            return None

        message = await visitor.response_bus.publish_message(
            session_id=visitor.session_id,
            content=content,
            channel=channel or visitor.channel,
            message_type=message_type,
            interaction_id=visitor.interaction.id if visitor.interaction else None,
            metadata=metadata,
        )

        # Link message to interaction
        if visitor.interaction:
            visitor.interaction.add_message(message.id)

        return message
