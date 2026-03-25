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
    are traversed by InteractWalker. These actions serve as modular points of
    execution that may exist in a prescribed chain of interact actions.

    The execute() method is automatically invoked by InteractWalker when it visits
    an InteractAction node. The walker performs routing checks first (if InteractRouter
    has executed), then automatically calls execute() if the action should run.

    Implementations should perform evaluation checks at the start of execute()
    and return early if conditions aren't met. This allows flexible, custom
    evaluation logic while keeping the API simple.

    Top-Level Action Routing:
        Top-level InteractActions (those directly connected to the Actions branch node)
        must employ logic to further route the InteractWalker to their children
        conditionally. The walker does not automatically traverse child InteractActions
        from top-level actions - this must be done explicitly within the action's execute()
        method using visitor.visit() or similar walker methods. This design allows for
        conditional routing based on the action's internal logic and state.

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
    description: str = attribute(default_factory=str, description="Action description")

    # Routing behavior hint: if True, this InteractAction should always be
    # allowed to execute regardless of routing results. InteractRouter will
    # treat such actions as dynamic routing exceptions.
    always_execute: bool = attribute(
        default=False,
        description=(
            "If True, this InteractAction must always be allowed to execute "
            "regardless of routing results (treated as a routing exception)."
        ),
    )

    # Background execution: if True, this action runs asynchronously after the
    # interaction is closed and the response has been sent to the client.
    run_in_background: bool = attribute(
        default=False,
        description=(
            "If True, this InteractAction is deferred and executed as a background "
            "task after the interaction is closed. The action does not block the "
            "user-facing response. Useful for analytics, model updates, and "
            "other non-critical post-interaction processing."
        ),
    )

    # Anchors for routing (published by InteractRouter)
    anchors: List[str] = attribute(
        default_factory=list,
        description=(
            "Anchor statements for routing. List of statements describing when this action should be used. "
            "The action's class/entity name is automatically used as the key when collected by InteractRouter."
        ),
    )

    # Parameters for behavioral guidance (prescribed parameters for this InteractAction)
    parameters: List[Dict[str, Any]] = attribute(
        default_factory=list,
        description=(
            "Standard collection of configurable parameters to apply when executing the action. "
            "Each parameter should have 'condition' and 'response' keys. These parameters can be "
            "prescribed to PersonaAction for behavioral guidance during response generation."
        ),
    )

    deny_access_directive: str = attribute(
        default_factory=str,
        description="Message shown to user when access is denied",
    )

    def _ensure_interaction(self, visitor: "InteractWalker") -> bool:
        """Check that visitor has a valid interaction.

        Returns:
            True if interaction is available, False otherwise.
            When False, caller should unrecord and return.
        """
        return visitor.interaction is not None

    async def get_anchors(
        self, conversation: Optional[Any] = None
    ) -> Optional[List[str]]:
        """Return dynamic anchors for this action, or None to use static self.anchors.

        Override this in subclasses that need user-specific or runtime-derived anchors
        (e.g. fetching memory category titles/keywords to inform the InteractRouter LLM).

        Args:
            conversation: Current Conversation node (may be None).

        Returns:
            A list of anchor strings to inject, or None to fall back to self.anchors.
        """
        return None

    @abstractmethod
    async def execute(self, visitor: "InteractWalker") -> None:
        """Execute the action's logic on the interaction.

        This method is conditionally called by InteractWalker when it visits this
        InteractAction node. The walker handles routing checks and conditional
        execution before invoking this method.

        Implementations should perform evaluation checks at the start and return
        early if conditions aren't met. This allows flexible, custom evaluation
        logic while keeping the API simple.

        Top-Level Action Routing:
            If this is a top-level InteractAction (directly connected to the Actions
            branch node) and it has child InteractActions, you MUST explicitly route
            the walker to those children within this method. The walker does not
            automatically traverse child actions from top-level actions.

        Example:
            async def execute(self, visitor: "InteractWalker") -> None:
                # Evaluation checks at the start
                if not self._should_run(visitor):
                    return  # Early return if conditions not met

                # Execution logic here
                interaction = visitor.interaction
                # ... perform action logic ...

                # If this is a top-level action with children, route explicitly
                if self._should_route_to_children(visitor):
                    child_action = await self.node(node="ChildInteractAction")
                    if child_action:
                        await visitor.visit(child_action)

        Args:
            visitor: The InteractWalker visiting this action

        Note:
            - This method is conditionally invoked by the walker - no @on_visit decorator needed
            - Access the Interaction via visitor.interaction
            - Access action properties via self (the node instance)
            - The walker performs routing checks before calling execute()
            - Top-level actions must explicitly route to children using visitor.visit()
        """
        pass

    async def publish(
        self,
        visitor: "InteractWalker",
        content: str,
        channel: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        streaming_complete: bool = True,
        stream: Optional[bool] = None,
        transient: bool = False,
    ) -> Optional[Any]:
        """Publish a response directly to the response bus via publish.

        Stream mode defaults to visitor.stream; pass stream=False to publish a complete
        message as a single adhoc (e.g. pre-built summary) so it is appended and enqueued
        immediately without going through the streaming accumulator.

        Args:
            visitor: The InteractWalker (provides interaction, response_bus, session_id, stream)
            content: Response content to publish
            channel: Target channel (defaults to visitor.channel)
            metadata: Additional metadata for the message
            streaming_complete: True for a single/final chunk; False if more chunks follow (only when stream=True)
            stream: If None, use visitor.stream; if False, publish as non-streaming (single adhoc).
            transient: If True, skip appending content to interaction.response.
                Use for transient messages (e.g., canned responses, typing indicators).

        Returns:
            ResponseMessage from ResponseBus.publish, or None if not published.
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
            logger.warning("Session ID not available - cannot publish response")
            return None

        interaction = visitor.interaction
        if not interaction:
            logger.warning(
                "Interaction not available - cannot publish response or set interaction.response"
            )
            return None

        use_stream = stream if stream is not None else getattr(visitor, "stream", False)
        pub_channel = channel or visitor.channel
        visitor_data = getattr(visitor, "data", None) or {}
        pub_metadata = {**(metadata or {}), **visitor_data}
        return await visitor.response_bus.publish(
            session_id=visitor.session_id,
            content=content,
            channel=pub_channel,
            stream=use_stream,
            interaction_id=interaction.id,
            interaction=interaction,
            user_id=interaction.user_id if hasattr(interaction, "user_id") else None,
            metadata=pub_metadata,
            streaming_complete=streaming_complete,
            transient=transient,
        )

    async def respond(
        self,
        visitor: "InteractWalker",
        directives: Optional[List[str]] = None,
        parameters: Optional[List[Dict[str, Any]]] = None,
        *,
        # Defaults match PersonaAction.respond() defaults
        use_history: bool = True,
        history_limit: int = 3,
        with_utterance: bool = True,
        with_interpretation: bool = False,
        with_event: bool = True,
        with_response: bool = True,
        max_statement_length: Optional[int] = None,
        transient: bool = False,
    ) -> Optional[str]:
        """Generate a response via PersonaAction with configurable history.

        This method retrieves PersonaAction and uses it to generate a response based on
        the current interaction's directives, parameters, and conversation history.
        The response is automatically set on the interaction and persisted.

        When the visitor has a response bus and session, the generated response is
        piped to the response bus; InteractActions can rely on calling respond() and
        having the response delivered to the bus without extra steps.

        Args:
            visitor: The InteractWalker (required, provides interaction and response_bus)
            use_history: Include conversation history (default: True)
            history_limit: Number of past interactions to include (default: 3)
            with_utterance: Include user utterance in prompt (default: True)
            with_interpretation: Include interpretations in history (default: False)
            with_event: Include events in history (default: True)
            with_response: Include AI responses in history (default: True)
            max_statement_length: Truncate utterances/responses to this length (default: None)
            transient: If True, skip appending response to interaction.response. Use for
                temporary messages like canned responses or typing indicators (default: False)
            directives: Optional list of directive strings to add to the interaction before
                generating the response. Each directive will be added with the current action's
                class name. If provided, these are added in addition to any existing directives.
            parameters: Optional list of parameter dictionaries to add to the interaction before
                generating the response. Each parameter should have 'condition' and 'response' keys.
                If provided, these are added in addition to any existing parameters.

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

            # With directives and parameters (simplified API)
            response = await self.respond(
                visitor,
                directives=["Use the provided context to answer the question"],
                parameters=[{
                    "condition": "No relevant context found",
                    "response": "Inform the user that no relevant information was found"
                }]
            )
        """
        interaction = visitor.interaction
        if not interaction:
            logger.error("InteractAction.respond: No interaction available in visitor")
            return None

        try:
            # Add directives if provided (using bulk method for efficiency)
            if directives:
                await visitor.add_directives(directives)

            # Add parameters if provided. Use caller's action name explicitly so parameters
            # are attributed correctly even when visitor._current_action may not match
            # (e.g. when another action's context is active).
            if parameters:
                action_name = getattr(
                    self, "get_class_name", lambda: self.__class__.__name__
                )()
                if interaction.add_parameters(parameters, action_name):
                    await interaction.save()

            from jvagent.action.persona.persona_action import PersonaAction

            persona = await self.get_action(PersonaAction)
            if not persona:
                logger.debug(
                    "InteractAction.respond: PersonaAction not found; skipping response generation"
                )
                return None

            # PersonaAction.respond uses visitor.stream to determine streaming behavior
            # Do NOT override - respect the walker's original stream setting
            # (e.g., WhatsApp walkers have stream=False for non-streaming responses)

            # Call PersonaAction with all history configuration parameters
            # PersonaAction.respond() sets interaction.response immediately after getting the response
            # (including waiting for streaming to complete) to ensure subsequent ad-hoc calls can see it in history
            response = await persona.respond(
                interaction,
                visitor=visitor,
                use_history=use_history,
                history_limit=history_limit,
                with_utterance=with_utterance,
                with_interpretation=with_interpretation,
                with_event=with_event,
                with_response=with_response,
                max_statement_length=max_statement_length,
                transient=transient,
            )

            return response
        except Exception as e:
            logger.error(
                f"InteractAction.respond: Error calling PersonaAction: {e}",
                exc_info=True,
            )
            return None
