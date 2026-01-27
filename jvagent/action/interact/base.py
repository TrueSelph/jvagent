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
    description: str = attribute(
        default_factory=str,
        description="Action description"
    )

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
        message_type: str = "final",
        channel: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Any]:
        """Publish a response directly to the response bus, bypassing PersonaAction.

        This method can be used as a drop-in replacement for `respond()` when you want to
        bypass PersonaAction and supply a response directly. It automatically detects
        streaming vs non-streaming mode and publishes appropriately.

        When used as a full response replacement (default behavior):
        - Streaming mode: Publishes content as `stream_chunk` message(s), then `final` to signal completion
        - Non-streaming mode: Publishes content as `adhoc` message and sets `interaction.response` for persistence

        For backward compatibility, if `message_type` is explicitly provided (not the default "final"),
        it will be respected and used as-is. This allows existing code that uses `publish()` for
        adhoc status updates to continue working.

        Args:
            visitor: The InteractWalker (required, provides interaction and response_bus)
            content: Response content to publish (required)
            message_type: Type of message ("adhoc", "final", "stream_chunk"). If not provided or
                set to "final", auto-detects streaming mode and uses appropriate type. If explicitly
                provided, uses the specified type (for backward compatibility with adhoc status updates).
            channel: Target channel (defaults to visitor.channel)
            metadata: Additional metadata for the message

        Returns:
            ResponseMessage object if published successfully, None otherwise. For streaming mode,
            returns the final message (the last one published).

        Examples:
            # Publish a full response (auto-detects streaming/non-streaming mode)
            await self.publish(visitor, content="Here is your response...")
            
            # Publish a full response in streaming mode (auto-detected)
            # Will publish as stream_chunk + final
            visitor.stream = True
            await self.publish(visitor, content="Streaming response...")
            
            # Publish a full response in non-streaming mode (auto-detected)
            # Will publish as adhoc and set interaction.response
            visitor.stream = False
            await self.publish(visitor, content="Non-streaming response...")
            
            # Publish an adhoc status update (explicit message_type for backward compatibility)
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

        interaction = visitor.interaction
        if not interaction:
            logger.warning(
                "Interaction not available - cannot publish response or set interaction.response"
            )

        # Auto-detect streaming mode if message_type is default ("final")
        # This allows backward compatibility: if message_type is explicitly provided, use it
        auto_detect_mode = (message_type == "final")
        
        if auto_detect_mode:
            # Detect streaming mode (same pattern as PersonaAction)
            streaming = bool(
                visitor
                and getattr(visitor, "stream", False)
                and visitor.response_bus
                and visitor.session_id
            )
            
            if streaming:
                # Streaming mode: publish as stream_chunk, then final
                logger.debug(
                    f"{self.get_class_name()}: Publishing response to ResponseBus (streaming mode)"
                )
                
                # Send the complete response as a stream_chunk
                await visitor.response_bus.publish_message(
                    session_id=visitor.session_id,
                    content=content,
                    channel=channel or visitor.channel,
                    message_type="stream_chunk",
                    interaction_id=interaction.id if interaction else None,
                    user_id=interaction.user_id if interaction and hasattr(interaction, "user_id") else None,
                    metadata=metadata,
                )
                
                # Send final to stop streaming indicator
                final_message = await visitor.response_bus.publish_message(
                    session_id=visitor.session_id,
                    content="",  # Final messages don't need content
                    channel=channel or visitor.channel,
                    message_type="final",
                    interaction_id=interaction.id if interaction else None,
                    user_id=interaction.user_id if interaction and hasattr(interaction, "user_id") else None,
                    metadata=metadata,
                )
                
                # Set interaction.response for persistence (so ConverseInteractAction can detect it)
                # This ensures has_response() returns True and prevents ConverseInteractAction from executing
                if interaction:
                    response_changed = interaction.set_response(content)
                    if response_changed:
                        await interaction.save()
                        logger.debug(
                            f"{self.get_class_name()}: Set interaction.response and saved interaction (streaming mode)"
                        )
                
                return final_message
            else:
                # Non-streaming mode: publish as adhoc and set interaction.response
                logger.debug(
                    f"{self.get_class_name()}: Publishing response to ResponseBus (non-streaming mode, adhoc)"
                )
                
                message = await visitor.response_bus.publish_message(
                    session_id=visitor.session_id,
                    content=content,
                    channel=channel or visitor.channel,
                    message_type="adhoc",
                    interaction_id=interaction.id if interaction else None,
                    user_id=interaction.user_id if interaction and hasattr(interaction, "user_id") else None,
                    metadata=metadata,
                )
                
                # Set interaction.response for persistence (so it's available before finalize_interaction)
                if interaction:
                    response_changed = interaction.set_response(content)
                    if response_changed:
                        await interaction.save()
                        logger.debug(
                            f"{self.get_class_name()}: Set interaction.response and saved interaction"
                        )
                
                return message
        else:
            # Backward compatibility: use explicitly provided message_type
            logger.debug(
                f"{self.get_class_name()}: Publishing message with explicit message_type={message_type}"
            )
            
            message = await visitor.response_bus.publish_message(
                session_id=visitor.session_id,
                content=content,
                channel=channel or visitor.channel,
                message_type=message_type,
                interaction_id=interaction.id if interaction else None,
                user_id=interaction.user_id if interaction and hasattr(interaction, "user_id") else None,
                metadata=metadata,
            )
            
            return message

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
        max_statement_length: Optional[int] = None
    ) -> Optional[str]:
        """Generate a response via PersonaAction with configurable history.

        This method retrieves PersonaAction and uses it to generate a response based on
        the current interaction's directives, parameters, and conversation history.
        The response is automatically set on the interaction and persisted.

        Args:
            visitor: The InteractWalker (required, provides interaction and response_bus)
            use_history: Include conversation history (default: True)
            history_limit: Number of past interactions to include (default: 3)
            with_utterance: Include user utterance in prompt (default: True)
            with_interpretation: Include interpretations in history (default: False)
            with_event: Include events in history (default: True)
            with_response: Include AI responses in history (default: True)
            max_statement_length: Truncate utterances/responses to this length (default: None)
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

            # Add parameters if provided (using bulk method for efficiency)
            if parameters:
                await visitor.add_parameters(parameters)

            from jvagent.action.persona.persona_action import PersonaAction
            persona = await self.get_action(PersonaAction)
            if not persona:
                logger.debug("InteractAction.respond: PersonaAction not found; skipping response generation")
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
            )


            return response
        except Exception as e:
            logger.error(f"InteractAction.respond: Error calling PersonaAction: {e}", exc_info=True)
            return None

