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



    async def publish(
        self,
        visitor: "InteractWalker",
        content: str,
        message_type: str = "final",
        channel: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Any]:
        """Publish a raw content-based response directly to the response bus.

        This method bypasses PersonaAction and publishes content directly to the response bus.
        It does NOT set the response on the interaction object - use this for adhoc messages,
        status updates, or responses that don't need to be persisted.

        Args:
            visitor: The InteractWalker (required, provides interaction and response_bus)
            content: Response content to publish (required)
            message_type: Type of message ("adhoc", "final", "stream_chunk")
            channel: Target channel (defaults to visitor.channel)
            metadata: Additional metadata for the message

        Returns:
            ResponseMessage object if published successfully, None otherwise

        Examples:
            # Publish a simple message
            await self.publish(visitor, content="Processing your request...")
            
            # Publish an adhoc status update
            await self.publish(
                visitor,
                content="Status: In progress",
                message_type="adhoc"
            )
            
            # Publish with custom metadata
            await self.publish(
                visitor,
                content="Task completed",
                metadata={"task_id": "123", "status": "success"}
            )
        """
        if not content:
            logger.error("InteractAction.publish: content is required")
            return None

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

    async def respond(
        self,
        visitor: "InteractWalker",
        *,
        # Defaults match PersonaAction.respond() defaults
        use_utterance: bool = True,
        use_history: bool = True,
        history_limit: int = 3,
        with_interpretation: bool = False,
        with_event: bool = False,
        with_response: bool = True,
        max_statement_length: Optional[int] = None,
    ) -> Optional[str]:
        """Generate a response via PersonaAction with configurable history.

        This method retrieves PersonaAction and uses it to generate a response based on
        the current interaction's directives, parameters, and conversation history.
        The response is automatically set on the interaction and persisted.

        Args:
            visitor: The InteractWalker (required, provides interaction and response_bus)
            use_utterance: Include user utterance in prompt (default: True)
            use_history: Include conversation history (default: False)
            history_limit: Number of past interactions to include (default: 3)
            with_interpretation: Include interpretations in history (default: False)
            with_event: Include events in history (default: False)
            with_response: Include AI responses in history (default: True)
            max_statement_length: Truncate utterances/responses to this length (default: None)

        Returns:
            Generated response string, or None if PersonaAction not found or error occurred

        Examples:
            # Basic response generation
            response = await self.respond(visitor)
            
            # With conversation history
            response = await self.respond(visitor, use_history=True, history_limit=5)
            
            # Include interpretations and events in history
            response = await self.respond(
                visitor,
                use_history=True,
                with_interpretation=True,
                with_event=True,
                history_limit=10
            )
            
            # With truncation for long conversations
            response = await self.respond(
                visitor,
                use_history=True,
                max_statement_length=500
            )
        """
        interaction = visitor.interaction
        if not interaction:
            logger.error("InteractAction.respond: No interaction available in visitor")
            return None

        try:
            from jvagent.action.persona.base import PersonaAction
            persona = await self.get_action(PersonaAction)
            if not persona:
                logger.debug("InteractAction.respond: PersonaAction not found; skipping response generation")
                return None

            # PersonaAction.respond supports visitor for streaming via ResponseBus
            visitor.stream_mode = True

            # Call PersonaAction with all history configuration parameters
            response = await persona.respond(
                interaction,
                visitor=visitor,
                use_utterance=use_utterance,
                use_history=use_history,
                history_limit=history_limit,
                with_interpretation=with_interpretation,
                with_event=with_event,
                with_response=with_response,
                max_statement_length=max_statement_length,
            )

            if response and interaction:
                interaction.set_response(response)
                await interaction.save()

            return response
        except Exception as e:
            logger.error(f"InteractAction.respond: Error calling PersonaAction: {e}", exc_info=True)
            return None

