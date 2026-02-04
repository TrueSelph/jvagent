"""State handlers for interview action.

This module contains handlers for generating directives based on interview state.
"""

import logging
from typing import TYPE_CHECKING, Any, Optional

from ..session.interview_session import InterviewSession
from ..graph.question_node import QuestionNode
from ..graph.question_walker import QuestionWalker
from ..processing.response_processor import ResponseProcessor
from .state_machine import InterviewStateMachine
from ..graph.state_node import StateNode
from ..foundation.enums import InterviewState, ValidationStatus, Intent
from ..utils.constants import CONTEXT_KEY_DIRECTIVE_OVERRIDE_REPLACE_MODE
from ..utils.session_utils import cleanup_session
from ..classification.intent_handlers import IntentHandlerRegistry

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.action.interview.interview_interact_action import ClassificationResult, InterviewInteractAction

logger = logging.getLogger(__name__)


class StateHandler:
    """Handler for generating directives based on interview state.
    
    This class encapsulates state-specific directive generation logic,
    taking the action instance and other dependencies as parameters.
    """
    
    def __init__(self, action: "InterviewInteractAction"):
        """Initialize state handler with action instance.
        
        Args:
            action: InterviewInteractAction instance
        """
        self.action = action
        self._intent_registry = None
    
    @property
    def intent_registry(self) -> IntentHandlerRegistry:
        """Get or create intent handler registry."""
        if self._intent_registry is None:
            self._intent_registry = IntentHandlerRegistry(self.action)
        return self._intent_registry
    
    async def generate_active_directive(
        self,
        session: InterviewSession,
        classification_result: "ClassificationResult",
        visitor: "InteractWalker",
        interaction: Any,
        state_machine: Optional[InterviewStateMachine] = None
    ) -> None:
        """Generate directive for ACTIVE state (question flow).

        Logic flow:
        - Use intent handlers to process intents
        - Use QuestionWalker to find next unanswered question
        - Generate directive from QuestionNode
        - Check if all required questions answered → transition to REVIEW

        Args:
            session: Interview session
            classification_result: Classification result
            visitor: InteractWalker
            interaction: Current interaction
            state_machine: Optional state machine for transitions
        """
        # Convert intent string to Intent enum
        try:
            intent = Intent(classification_result.intent)
        except ValueError:
            intent = Intent.NONE
        
        # Handle intent using handler registry
        handler_result = await self.intent_registry.handle(
            intent,
            session,
            classification_result,
            visitor,
            interaction,
            state_machine
        )
        
        # If handler indicates we shouldn't continue, return early
        if not handler_result.should_continue:
            return
        
        # If we just handled a decline and should continue, clear active_question_key
        # This ensures we find the next question correctly and prevents question path disruption
        if intent == Intent.DECLINE and handler_result.should_continue:
            session.active_question_key = None
            await session.save()
            logger.debug(
                f"{self.action.get_class_name()}: Cleared active_question_key after DECLINE handling "
                f"for field '{classification_result.field}'"
            )
        
        # If state changed during handling (e.g., transition to REVIEW), handle new state
        if session.state != InterviewState.ACTIVE:
            target_state = session.state
            if target_state == InterviewState.REVIEW:
                await self.generate_review_directive(session, classification_result, visitor, state_machine)
            elif target_state == InterviewState.COMPLETED:
                await self.generate_completed_directive(session, visitor)
            elif target_state == InterviewState.CANCELLED:
                await self.generate_cancelled_directive(session, visitor)
            return
        
        # If active_question_key is set to an unanswered field (invalid response), return
        # UNLESS we just updated a field - in that case, continue to find next question
        if session.active_question_key and session.active_question_key in session.get_unanswered_questions():
            # Don't return early if we just updated a field - continue to find next question
            if not handler_result.updated_field:
                return
        
        updated_field = handler_result.updated_field
        
        # If we just updated a field and active_question_key was set to that field, clear it
        # This ensures we find the next question correctly after an update
        if updated_field and session.active_question_key == updated_field:
            session.active_question_key = None
            await session.save()

        # Get directive for next node (question or state) using QuestionWalker
        question_walker = QuestionWalker()
        question_walker.interview_session = session
        question_walker.interaction = interaction
        question_walker.question_directive_template = self.action.config.templates.question_directive
        
        start_from = session.active_question_key if session.active_question_key in session.get_unanswered_questions() else None
        node = await question_walker.find_next_question(session, interview_action=self.action, start_from=start_from)
        # Handle StateNode - execute transition and generate directive
        if isinstance(node, StateNode):
            # Execute state transition
            await node.execute(session, question_walker)
            session.active_question_key = None
            await session.save()
            
            # Generate directive based on state type
            if node.state_type == InterviewState.REVIEW:
                await self.generate_review_directive(session, classification_result, visitor, state_machine)
            elif node.state_type == InterviewState.COMPLETED:
                await self.generate_completed_directive(session, visitor)
            elif node.state_type == InterviewState.CANCELLED:
                await self.generate_cancelled_directive(session, visitor)
            return
        
        # Handle QuestionNode - execute and queue directive
        if isinstance(node, QuestionNode):
            directive = await node.execute(question_walker)
            if directive:
                # If an update was just handled, prepend a brief confirmation
                if updated_field:
                    field_display = updated_field.replace("_", " ").title()
                    new_value = session.get_response(updated_field)
                    confirmation = f"Tell the user: Updated {field_display} to {new_value}. "
                    directive = confirmation + directive
                await self.action.directive_builder.queue_directive(visitor, directive)
            return
        
        # node is None - graph is incomplete (error condition)
        logger.error(
            f"{self.action.get_class_name()}: Graph traversal returned None. "
            f"This indicates an incomplete graph definition. "
            f"All questions may be answered but no StateNode edge exists."
        )
        # Emergency fallback: transition to REVIEW if all questions answered
        unanswered = session.get_unanswered_questions()
        if not unanswered:
            session.active_question_key = None
            # Always use state machine for transitions
            if not state_machine:
                state_machine = InterviewStateMachine(session)
            try:
                state_machine.transition_to(InterviewState.REVIEW, reason="Emergency fallback: all questions answered")
                await session.save()
            except ValueError as e:
                logger.error(f"{self.action.get_class_name()}: Failed emergency transition to REVIEW: {e}", exc_info=True)
            await self.generate_review_directive(session, classification_result, visitor, state_machine)
    
    async def generate_review_directive(
        self,
        session: InterviewSession,
        classification_result: "ClassificationResult",
        visitor: "InteractWalker",
        state_machine: Optional[InterviewStateMachine] = None
    ) -> None:
        """Generate directive for REVIEW state (summary and confirmation).

        Logic flow:
        - Show summary for user to review
        - Handle UPDATE intent: process update and show updated summary
        - Handle CONFIRMATION intent: transition to COMPLETED (handled at top level)
        - Handle unclear responses: prompt for clarification

        Args:
            session: Interview session
            classification_result: Classification result
            visitor: InteractWalker
            state_machine: Optional state machine for transitions
        """
        # Note: CONFIRMATION intent is handled at the top level of generate_directive
        if classification_result.intent == Intent.CONFIRMATION:
            logger.warning(f"{self.action.get_class_name()}: CONFIRMATION intent reached generate_review_directive, should have been handled earlier")
            # Always use state machine for transitions
            if not state_machine:
                state_machine = InterviewStateMachine(session)
            try:
                state_machine.transition_to(InterviewState.COMPLETED, reason="User confirmation (fallback)")
                await session.save()
            except ValueError as e:
                logger.error(f"{self.action.get_class_name()}: Failed fallback transition to COMPLETED: {e}", exc_info=True)
            await self.generate_completed_directive(session, visitor)
            return

        # Convert intent string to Intent enum
        try:
            intent = Intent(classification_result.intent)
        except ValueError:
            intent = Intent.NONE
        
        # Handle UPDATE intent using handler
        if intent == Intent.UPDATE:
            interaction = visitor.interaction
            handler_result = await self.intent_registry.handle(
                intent,
                session,
                classification_result,
                visitor,
                interaction,
                state_machine
            )
            
            if handler_result.handled:
                if not handler_result.should_continue:
                    # Handler completed everything (e.g., waiting for value)
                    return
                
                # Update completed, show updated summary (REVIEW state specific)
                # Check if replace mode override was used (already checked in handler)
                replace_mode_used = (session.context or {}).get(CONTEXT_KEY_DIRECTIVE_OVERRIDE_REPLACE_MODE, False)
                if not replace_mode_used:
                    # Show updated summary immediately in same turn
                    directive = await self.action.directive_builder.build_confirmation_directive(session)
                    await self.action.directive_builder.queue_directive(visitor, directive)
            return

        # Handle unclear response (NONE intent or other) using handler
        if intent == Intent.NONE or not classification_result.intent:
            interaction = visitor.interaction
            handler_result = await self.intent_registry.handle(
                intent,
                session,
                classification_result,
                visitor,
                interaction,
                state_machine
            )
            # NoneHandler in REVIEW state shows unclear content and returns should_continue=False
            if handler_result.handled:
                return

        # Default: Show summary for review (first entry to REVIEW state)
        directive = await self.action.directive_builder.build_confirmation_directive(session)
        await self.action.directive_builder.queue_directive(visitor, directive)

    async def generate_completed_directive(
        self,
        session: InterviewSession,
        visitor: "InteractWalker"
    ) -> None:
        """Generate directive for COMPLETED state.

        Delegates to DirectiveBuilder.

        Args:
            session: Interview session
            visitor: InteractWalker
        """
        await self.action.directive_builder.generate_completed_directive(session, visitor)

    async def generate_cancelled_directive(
        self,
        session: InterviewSession,
        visitor: "InteractWalker"
    ) -> None:
        """Generate directive for CANCELLED state.

        Delegates to DirectiveBuilder.

        Args:
            session: Interview session
            visitor: InteractWalker
        """
        await self.action.directive_builder.generate_cancelled_directive(session, visitor)
