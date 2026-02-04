"""Intent handlers for interview action.

Extracted intent handling logic using strategy pattern for better separation
of concerns and testability.
"""

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Optional

from ..foundation.enums import Intent, InterviewState, ValidationStatus
from ..session.interview_session import InterviewSession
from ..processing.response_processor import ResponseProcessor
from ..utils.constants import (
    CONTEXT_KEY_DIRECTIVE_OVERRIDE_REPLACE_MODE,
    CONTEXT_KEY_DIRECTIVE_OVERRIDE_APPEND_MODE
)

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.action.interview.interview_interact_action import ClassificationResult, InterviewInteractAction
    from jvagent.memory import Interaction

logger = logging.getLogger(__name__)


class HandlerResult:
    """Result from intent handler execution."""
    
    def __init__(
        self,
        handled: bool,
        should_continue: bool = True,
        updated_field: Optional[str] = None,
        message: Optional[str] = None
    ):
        """Initialize handler result.
        
        Args:
            handled: Whether the intent was successfully handled
            should_continue: Whether to continue with normal flow (default: True)
            updated_field: Optional field name that was updated
            message: Optional message to display
        """
        self.handled = handled
        self.should_continue = should_continue
        self.updated_field = updated_field
        self.message = message


class IntentHandler(ABC):
    """Base class for intent handlers."""
    
    def __init__(self, action: "InterviewInteractAction"):
        """Initialize intent handler with action instance.
        
        Args:
            action: InterviewInteractAction instance
        """
        self.action = action
    
    @abstractmethod
    async def handle(
        self,
        session: InterviewSession,
        result: "ClassificationResult",
        visitor: "InteractWalker",
        interaction: "Interaction",
        state_machine: Optional[Any] = None
    ) -> HandlerResult:
        """Handle the intent.
        
        Args:
            session: Interview session
            result: Classification result
            visitor: InteractWalker
            interaction: Current interaction
            state_machine: Optional state machine for transitions
            
        Returns:
            HandlerResult indicating success and whether to continue
        """
        pass


class CancellationHandler(IntentHandler):
    """Handler for CANCELLATION intent."""
    
    async def handle(
        self,
        session: InterviewSession,
        result: "ClassificationResult",
        visitor: "InteractWalker",
        interaction: "Interaction",
        state_machine: Optional[Any] = None
    ) -> HandlerResult:
        """Handle cancellation intent.
        
        Cancellation can occur in any state and immediately transitions to CANCELLED.
        """
        from ..state.state_machine import InterviewStateMachine
        
        if session.state == InterviewState.CANCELLED:
            # Already cancelled, nothing to do
            return HandlerResult(handled=True, should_continue=False)
        
        # Use state machine for transition
        if not state_machine:
            state_machine = InterviewStateMachine(session)
        
        success = await state_machine.safe_transition_to(
            InterviewState.CANCELLED,
            reason="User cancellation",
            context=self.action.get_class_name()
        )
        return HandlerResult(handled=success, should_continue=False)


class ConfirmationHandler(IntentHandler):
    """Handler for CONFIRMATION intent (only valid in REVIEW state)."""
    
    async def handle(
        self,
        session: InterviewSession,
        result: "ClassificationResult",
        visitor: "InteractWalker",
        interaction: "Interaction",
        state_machine: Optional[Any] = None
    ) -> HandlerResult:
        """Handle confirmation intent.
        
        Confirmation only valid in REVIEW state, transitions to COMPLETED.
        """
        from .state_machine import InterviewStateMachine
        
        if session.state != InterviewState.REVIEW:
            logger.warning(
                f"{self.action.get_class_name()}: CONFIRMATION intent in {session.state.value} state, "
                f"only valid in REVIEW state"
            )
            return HandlerResult(handled=False, should_continue=True)
        
        # Use state machine for transition
        if not state_machine:
            state_machine = InterviewStateMachine(session)
        
        success = await state_machine.safe_transition_to(
            InterviewState.COMPLETED,
            reason="User confirmation",
            context=self.action.get_class_name()
        )
        return HandlerResult(handled=success, should_continue=not success)


class UpdateHandler(IntentHandler):
    """Handler for UPDATE intent."""
    
    async def handle(
        self,
        session: InterviewSession,
        result: "ClassificationResult",
        visitor: "InteractWalker",
        interaction: "Interaction",
        state_machine: Optional[Any] = None
    ) -> HandlerResult:
        """Handle update intent.
        
        Processes field updates using QuestionNode validation.
        """
        # Normalize field - handle string "null" or empty string
        field = result.field
        if field and isinstance(field, str):
            field = field.strip()
            if field.lower() in ("null", "none", ""):
                field = None
                result.field = None
        
        # Check if field needs clarification
        if not field:
            # Ask which field to update - show summary for context
            answered_fields = session.get_answered_questions()
            if not answered_fields:
                logger.warning(
                    f"{self.action.get_class_name()}: UPDATE intent with null field but no answered fields"
                )
                return HandlerResult(handled=False, should_continue=True)
            
            field_list = ", ".join([f.replace("_", " ") for f in answered_fields])
            summary = self.action.directive_builder.format_summary(session)
            
            unclear_edit_section = self.action.unclear_edit_content_template.format(
                summary=summary,
                field_list=field_list
            )
            directive = self.action.review_directive_template.format(
                confirmation_section="",
                unclear_edit_section=unclear_edit_section,
                unclear_general_section="",
            )
            await self.action.directive_builder.queue_directive(visitor, directive)
            return HandlerResult(handled=True, should_continue=False)
        
        # Handle the update inline (reusing QuestionNode processing/validation)
        response_processor = ResponseProcessor(self.action)
        update_completed = await response_processor.handle_update_inline(
            result,
            session,
            visitor,
            interaction
        )
        
        if not update_completed:
            # Waiting for value or clarification
            return HandlerResult(handled=True, should_continue=False)
        
        # Check if replace mode override was used
        replace_mode_used = (session.context or {}).get(CONTEXT_KEY_DIRECTIVE_OVERRIDE_REPLACE_MODE, False)
        if replace_mode_used:
            # Clear the flag
            session.context.pop(CONTEXT_KEY_DIRECTIVE_OVERRIDE_REPLACE_MODE, None)
            await session.save()
            # Don't find next question - replace mode override already handled the response
            return HandlerResult(handled=True, should_continue=False, updated_field=field)
        
        return HandlerResult(handled=True, should_continue=True, updated_field=field)


class DeclineHandler(IntentHandler):
    """Handler for DECLINE intent."""
    
    async def handle(
        self,
        session: InterviewSession,
        result: "ClassificationResult",
        visitor: "InteractWalker",
        interaction: "Interaction",
        state_machine: Optional[Any] = None
    ) -> HandlerResult:
        """Handle decline intent.
        
        Handles user declining to answer optional questions.
        """
        field = result.field
        if field and isinstance(field, str):
            field = field.strip()
            if field.lower() in ("null", "none", ""):
                field = None
        
        # If field not specified, try to use active question as fallback
        if not field and session.active_question_key:
            field = session.active_question_key
        
        if not field:
            # Field not specified and no active question - treat as unclear response
            logger.warning(
                f"{self.action.get_class_name()}: DECLINE intent without field specified "
                f"and no active question"
            )
            return HandlerResult(handled=False, should_continue=True)
        
        # Check if field is required and if it's a data_input_field question
        question_config = session.get_question_by_name(field)
        is_required = question_config.get("required", False) if question_config else False
        
        if is_required:
            # Required field - insist on answer
            field_display = field.replace("_", " ").title()
            question_text = question_config.get("question", field_display) if question_config else field_display
            
            # Generate directive using required_field_decline_template
            directive = self.action.required_field_decline_template.format(
                field_display=field_display,
                question=question_text
            )
            
            # Keep active_question_key pointing to this required field
            session.active_question_key = field
            await session.save()
            
            await self.action.directive_builder.queue_directive(visitor, directive)
            return HandlerResult(handled=True, should_continue=False)
        else:
            # Non-required field - store decline value (configurable) and continue
            decline_value = self.action.config.classification.decline_value
            session.set_response(field, decline_value)
            session.set_validation_status(field, ValidationStatus.VALID)
            await session.save()
            
            # Re-evaluate branches after storing decline value
            # This ensures conditional flow respects the declined field
            # and prevents question path disruption
            from ..graph.question_walker import QuestionWalker
            question_walker = QuestionWalker()
            question_walker.interview_session = session
            question_walker.interaction = interaction
            await self.action._update_reachable_questions(session, question_walker, just_answered_field=field)
            
            logger.debug(
                f"{self.action.get_class_name()}: Declined non-required field {field}, stored as '{decline_value}'. "
                f"Branches re-evaluated to maintain question path integrity."
            )
            return HandlerResult(handled=True, should_continue=True)


class SubmissionHandler(IntentHandler):
    """Handler for SUBMISSION intent."""
    
    async def handle(
        self,
        session: InterviewSession,
        result: "ClassificationResult",
        visitor: "InteractWalker",
        interaction: "Interaction",
        state_machine: Optional[Any] = None
    ) -> HandlerResult:
        """Handle submission intent.
        
        Processes and validates extracted field values.
        """
        if not result.extracted_data:
            return HandlerResult(handled=False, should_continue=True)
        
        # Capture state before processing responses
        state_before_processing = session.state
        
        # Validate and store responses
        from ..graph.question_walker import QuestionWalker
        question_walker = QuestionWalker()
        question_walker.interview_session = session
        question_walker.interaction = interaction
        question_walker.question_directive_template = self.action.question_directive_template
        
        response_processor = ResponseProcessor(self.action)
        await response_processor.process_responses_to_questions(
            result.extracted_data,
            session,
            visitor,
            interaction,
            question_walker
        )
        
        # Check if state transition occurred during response processing
        state_changed = session.state != state_before_processing

        if state_changed:
            # State transition occurred via StateNode.execute() during branch evaluation
            return HandlerResult(handled=True, should_continue=False)
        
        # If active_question_key is set to an unanswered field (invalid response), return
        if session.active_question_key and session.active_question_key in session.get_unanswered_questions():
            return HandlerResult(handled=True, should_continue=False)
        
        # Check if replace mode override was used
        replace_mode_used = (session.context or {}).get(CONTEXT_KEY_DIRECTIVE_OVERRIDE_REPLACE_MODE, False)
        if replace_mode_used:
            # Clear the flag
            session.context.pop(CONTEXT_KEY_DIRECTIVE_OVERRIDE_REPLACE_MODE, None)
            await session.save()
            # Don't find next question - replace mode override already handled the response
            return HandlerResult(handled=True, should_continue=False)
        
        # Check if append mode override was used
        append_mode_used = (session.context or {}).get(CONTEXT_KEY_DIRECTIVE_OVERRIDE_APPEND_MODE, False)
        if append_mode_used:
            # Clear the flag
            session.context.pop(CONTEXT_KEY_DIRECTIVE_OVERRIDE_APPEND_MODE, None)
            await session.save()
            # Don't find next question - append mode override already handled it
            return HandlerResult(handled=True, should_continue=False)
        
        return HandlerResult(handled=True, should_continue=True)


class NoneHandler(IntentHandler):
    """Handler for NONE intent (unclear responses)."""
    
    async def handle(
        self,
        session: InterviewSession,
        result: "ClassificationResult",
        visitor: "InteractWalker",
        interaction: "Interaction",
        state_machine: Optional[Any] = None
    ) -> HandlerResult:
        """Handle none/unclear intent.
        
        For unclear responses, behavior depends on current state.
        """
        # In REVIEW state, show unclear general content
        if session.state == InterviewState.REVIEW:
            directive = self.action.review_directive_template.format(
                confirmation_section="",
                unclear_edit_section="",
                unclear_general_section=self.action.unclear_general_content_template,
            )
            await self.action.directive_builder.queue_directive(visitor, directive)
            return HandlerResult(handled=True, should_continue=False)
        
        # In ACTIVE state, unclear responses are typically handled by re-asking the question
        # Let normal flow handle it
        return HandlerResult(handled=False, should_continue=True)


class IntentHandlerRegistry:
    """Registry for intent handlers."""
    
    def __init__(self, action: "InterviewInteractAction"):
        """Initialize registry with action instance.
        
        Args:
            action: InterviewInteractAction instance
        """
        self.action = action
        self._handlers = {
            Intent.CANCELLATION: CancellationHandler(action),
            Intent.CONFIRMATION: ConfirmationHandler(action),
            Intent.UPDATE: UpdateHandler(action),
            Intent.DECLINE: DeclineHandler(action),
            Intent.SUBMISSION: SubmissionHandler(action),
            Intent.NONE: NoneHandler(action),
        }
    
    def get_handler(self, intent: Intent) -> Optional[IntentHandler]:
        """Get handler for an intent.
        
        Args:
            intent: Intent enum value
            
        Returns:
            IntentHandler if found, None otherwise
        """
        return self._handlers.get(intent)
    
    async def handle(
        self,
        intent: Intent,
        session: InterviewSession,
        result: "ClassificationResult",
        visitor: "InteractWalker",
        interaction: "Interaction",
        state_machine: Optional[Any] = None
    ) -> HandlerResult:
        """Handle an intent using the appropriate handler.
        
        Args:
            intent: Intent to handle
            session: Interview session
            result: Classification result
            visitor: InteractWalker
            interaction: Current interaction
            state_machine: Optional state machine for transitions
            
        Returns:
            HandlerResult indicating success and whether to continue
        """
        handler = self.get_handler(intent)
        if handler:
            return await handler.handle(session, result, visitor, interaction, state_machine)
        
        # No handler found, return unhandled
        logger.warning(f"{self.action.get_class_name()}: No handler found for intent {intent.value}")
        return HandlerResult(handled=False, should_continue=True)
