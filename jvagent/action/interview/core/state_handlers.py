"""State handlers for interview action.

This module contains handlers for generating directives based on interview state.
"""

import logging
from typing import TYPE_CHECKING, Any, Optional

from .interview_session import InterviewSession
from .question_walker import QuestionWalker
from .response_processor import ResponseProcessor
from .state_machine import InterviewStateMachine
from .enums import InterviewState, ValidationStatus, Intent, ContextKey

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
        - If responses extracted: validate and store via QuestionNode
        - If update handled: confirm update
        - Use QuestionWalker to find next unanswered question
        - Generate directive from QuestionNode
        - Check if all required questions answered → transition to REVIEW

        Args:
            session: Interview session
            classification_result: Classification result
            visitor: InteractWalker
            interaction: Current interaction
        """
        updated_field = None

        # Handle update intent
        if classification_result.intent == Intent.UPDATE:
            # Normalize field - handle string "null" or empty string
            field = classification_result.field
            if field and isinstance(field, str):
                field = field.strip()
                if field.lower() in ("null", "none", ""):
                    field = None
                    classification_result.field = None

            # Check if field needs clarification
            if not field:
                # Ask which field to update - show summary for context
                answered_fields = session.get_answered_questions()
                field_list = ", ".join([f.replace("_", " ") for f in answered_fields])
                summary = self.action._format_summary(session)
                unclear_edit_section = self.action.unclear_edit_content_template.format(
                    summary=summary,
                    field_list=field_list
                )
                directive = self.action.review_directive_template.format(
                    confirmation_section="",
                    unclear_edit_section=unclear_edit_section,
                    unclear_general_section="",
                )
                await self.action._queue_directive(visitor, directive)
                return

            # Handle the update inline (reusing QuestionNode processing/validation)
            response_processor = ResponseProcessor(self.action)
            update_completed = await response_processor.handle_update_inline(
                classification_result,
                session,
                visitor,
                interaction
            )

            if not update_completed:
                # Waiting for value or clarification
                return

            # Check if replace mode override was used
            replace_mode_used = (session.context or {}).get(ContextKey.DIRECTIVE_OVERRIDE_REPLACE_MODE, False)
            if replace_mode_used:
                # Clear the flag
                session.context.pop(ContextKey.DIRECTIVE_OVERRIDE_REPLACE_MODE, None)
                await session.save()
                # Don't find next question - replace mode override already handled the response
                return

            updated_field = classification_result.field

        # Handle decline intent
        elif classification_result.intent == Intent.DECLINE:
            field = classification_result.field
            if field and isinstance(field, str):
                field = field.strip()
                if field.lower() in ("null", "none", ""):
                    field = None

            # If field not specified, try to use active question as fallback
            if not field and session.active_question_key:
                field = session.active_question_key
                logger.debug(f"{self.action.get_class_name()}: DECLINE intent without field specified, using active question: {field}")

            if not field:
                # Field not specified and no active question - treat as unclear response
                logger.warning(f"{self.action.get_class_name()}: DECLINE intent without field specified and no active question")
            else:
                # Check if field is required
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

                    await self.action._queue_directive(visitor, directive)
                    return  # Don't advance to next question
                else:
                    # Non-required field - store "n/a" and continue
                    session.set_response(field, "n/a")
                    session.set_validation_status(field, ValidationStatus.VALID)
                    await session.save()
                    logger.debug(f"{self.action.get_class_name()}: Declined non-required field {field}, stored as 'n/a'")

        # Handle response extraction
        elif classification_result.intent == Intent.SUBMISSION and classification_result.extracted_data:
            # Validate and store responses
            question_walker = QuestionWalker()
            question_walker.interview_session = session
            question_walker.interaction = interaction
            question_walker.question_directive_template = self.action.question_directive_template

            response_processor = ResponseProcessor(self.action)
            await response_processor.process_responses_to_questions(
                classification_result.extracted_data,
                session,
                visitor,
                interaction,
                question_walker
            )

            # If active_question_key is set to an unanswered field (invalid response), return
            if session.active_question_key and session.active_question_key in session.get_unanswered_questions():
                return

            # Check if replace mode override was used
            replace_mode_used = (session.context or {}).get(ContextKey.DIRECTIVE_OVERRIDE_REPLACE_MODE, False)
            if replace_mode_used:
                # Clear the flag
                session.context.pop(ContextKey.DIRECTIVE_OVERRIDE_REPLACE_MODE, None)
                await session.save()
                # Don't find next question - replace mode override already handled the response
                return

            # Check if append mode override was used
            append_mode_used = (session.context or {}).get(ContextKey.DIRECTIVE_OVERRIDE_APPEND_MODE, False)
            if append_mode_used:
                # Clear the flag
                session.context.pop(ContextKey.DIRECTIVE_OVERRIDE_APPEND_MODE, None)
                await session.save()
                # Don't find next question - append mode override already handled it
                return

        # Get directive for next question using QuestionWalker
        question_walker = QuestionWalker()
        question_walker.interview_session = session
        question_walker.interaction = interaction
        question_walker.question_directive_template = self.action.question_directive_template

        unanswered = session.get_unanswered_questions()
        if not unanswered:
            # All questions answered (or declined), transition to REVIEW
            session.active_question_key = None
            if state_machine:
                state_machine.transition_to(InterviewState.REVIEW, reason="All questions answered")
            else:
                session.transition_to(InterviewState.REVIEW)
            await session.save()
            return

        start_from = session.active_question_key if session.active_question_key in unanswered else None
        question_node = await question_walker.find_next_question(session, interview_action=self.action, start_from=start_from)
        
        # Check if a state target was encountered during traversal
        state_target = (session.context or {}).get("_state_target")
        if state_target:
            # Clear the state target from context
            session.context.pop("_state_target", None)
            await session.save()
            
            # Get the InterviewState from the state target
            target_state = question_walker._get_state_from_target(state_target)
            if target_state:
                # Transition to the target state
                session.active_question_key = None
                if state_machine:
                    state_machine.transition_to(target_state, reason=f"State target from question traversal")
                else:
                    session.transition_to(target_state)
                await session.save()
                
                # Generate directive for the new state
                if target_state == InterviewState.REVIEW:
                    await self.generate_review_directive(session, classification_result, visitor, state_machine)
                elif target_state == InterviewState.COMPLETED:
                    await self.generate_completed_directive(session, visitor)
                return
        
        if question_node:
            directive = await question_node.execute(question_walker)
            if directive:
                # If an update was just handled, prepend a brief confirmation
                if updated_field:
                    field_display = updated_field.replace("_", " ").title()
                    new_value = session.get_response(updated_field)
                    confirmation = f"Tell the user: Updated {field_display} to {new_value}. "
                    directive = confirmation + directive
                await self.action._queue_directive(visitor, directive)
        elif not state_target:
            # No question found and no state target - check if all required questions on path are answered
            if await session.has_all_required_answers(question_walker):
                # All required questions on the active path are answered, transition to REVIEW
                session.active_question_key = None
                if state_machine:
                    state_machine.transition_to(InterviewState.REVIEW, reason="All required questions answered")
                else:
                    session.transition_to(InterviewState.REVIEW)
                await session.save()
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
        """
        # Note: CONFIRMATION intent is handled at the top level of generate_directive
        if classification_result.intent == Intent.CONFIRMATION:
            logger.warning(f"{self.action.get_class_name()}: CONFIRMATION intent reached generate_review_directive, should have been handled earlier")
            if state_machine:
                state_machine.transition_to(InterviewState.COMPLETED, reason="User confirmation (fallback)")
            else:
                session.transition_to(InterviewState.COMPLETED)
            await session.save()
            await self.generate_completed_directive(session, visitor)
            return

        # Handle update
        if classification_result.intent == Intent.UPDATE:
            # Normalize field - handle string "null" or empty string
            field = classification_result.field
            if field and isinstance(field, str):
                field = field.strip()
                if field.lower() in ("null", "none", ""):
                    field = None
                    classification_result.field = None

            if not field:
                # Ask which field to update - show summary for context
                answered_fields = session.get_answered_questions()
                if not answered_fields:
                    logger.warning(f"{self.action.get_class_name()}: UPDATE intent with null field but no answered fields")
                    directive = self.action.review_directive_template.format(
                        confirmation_section="",
                        unclear_edit_section="",
                        unclear_general_section=self.action.unclear_general_content_template,
                    )
                    await self.action._queue_directive(visitor, directive)
                    return

                field_list = ", ".join([f.replace("_", " ") for f in answered_fields])
                summary = self.action._format_summary(session)

                # Ensure summary is not empty
                if not summary or not summary.strip():
                    summary = "No information available to review."

                unclear_edit_section = self.action.unclear_edit_content_template.format(
                    summary=summary,
                    field_list=field_list
                )
                directive = self.action.review_directive_template.format(
                    confirmation_section="",
                    unclear_edit_section=unclear_edit_section,
                    unclear_general_section="",
                )

                await self.action._queue_directive(visitor, directive)
                return

            # Handle the update inline
            interaction = visitor.interaction
            response_processor = ResponseProcessor(self.action)
            update_completed = await response_processor.handle_update_inline(
                classification_result,
                session,
                visitor,
                interaction
            )

            if update_completed:
                # Check if replace mode override was used
                replace_mode_used = (session.context or {}).get(ContextKey.DIRECTIVE_OVERRIDE_REPLACE_MODE, False)
                if replace_mode_used:
                    # Clear the flag
                    session.context.pop(ContextKey.DIRECTIVE_OVERRIDE_REPLACE_MODE, None)
                    await session.save()
                    # Don't show summary - replace mode override already handled the response
                    return
                
                # Show updated summary immediately in same turn
                directive = self.action._build_confirmation_directive(session)
                await self.action._queue_directive(visitor, directive)
            return

        # Handle unclear response (NONE intent or other)
        if classification_result.intent == Intent.NONE or not classification_result.intent:
            directive = self.action.review_directive_template.format(
                confirmation_section="",
                unclear_edit_section="",
                unclear_general_section=self.action.unclear_general_content_template,
            )
            await self.action._queue_directive(visitor, directive)
            return

        # Default: Show summary for review (first entry to REVIEW state)
        directive = self.action._build_confirmation_directive(session)
        await self.action._queue_directive(visitor, directive)

    async def generate_completed_directive(
        self,
        session: InterviewSession,
        visitor: "InteractWalker"
    ) -> None:
        """Generate directive for COMPLETED state.

        Calls registered completion handler and cleans up session.

        Args:
            session: Interview session
            visitor: InteractWalker
        """
        # Explicitly add completion event BEFORE cleaning up the session
        completion_event = self.action.completion_event_message_template.format(class_name=self.action.get_class_name())
        await visitor.add_event(completion_event)
        self.action._event_added = True

        # Get completion handler for this interview type
        interview_type = session.interview_type
        completion_handler = self.action.get_completion_handler(interview_type)

        if completion_handler:
            try:
                await completion_handler(session, visitor, self.action)
            except Exception as e:
                logger.error(f"{self.action.get_class_name()}: Completion handler failed: {e}", exc_info=True)
                await self.action._queue_directive(
                    visitor,
                    self.action.completion_message_template
                )
        else:
            # No completion handler registered, send generic message
            await self.action._queue_directive(
                visitor,
                self.action.completion_message_template
            )

        # Clean up and remove the session
        try:
            await session.cleanup()
            visitor.interview_session = None
        except Exception as e:
            logger.error(f"{self.action.get_class_name()}: Failed to cleanup completed session: {e}", exc_info=True)

    async def generate_cancelled_directive(
        self,
        session: InterviewSession,
        visitor: "InteractWalker"
    ) -> None:
        """Generate directive for CANCELLED state.

        Sends cancellation acknowledgment and removes/clears the session.

        Args:
            session: Interview session
            visitor: InteractWalker
        """
        # Send cancellation message first
        await self.action._queue_directive(
            visitor,
            self.action.cancellation_message_template
        )

        # Clean up and remove the session
        try:
            await session.cleanup()
            visitor.interview_session = None
        except Exception as e:
            logger.error(f"{self.action.get_class_name()}: Failed to cleanup cancelled session: {e}", exc_info=True)
