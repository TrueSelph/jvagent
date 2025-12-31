"""Interview Action Implementation

Unified interview system for gathering structured information from users through
multi-turn conversations with validation, revision, and confirmation flows.

This is an abstract base class that should be extended to create concrete
interview implementations. Each subclass should define its own question_index
with the questions for that interview flow.

The system uses a unified classification and extraction approach that detects
user intent (CANCELLATION, CONFIRMATION, UPDATE, SUBMISSION, NONE) and extracts
field values in a single LLM call. All state management and directive generation
is handled within the main InterviewInteractAction class.
"""

import json
import logging
import re
from abc import ABC
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from jvagent.action.interact.base import InteractAction
from jvagent.memory import Interaction
from jvspatial.core.annotations import attribute

from .core.interview_session import InterviewSession
from .core.question_node import QuestionNode
from .core.question_walker import QuestionWalker
from .core.validation import InterviewState, ValidationStatus
from .prompts import (
    UPDATE_PROMPT_FOR_VALUE_TEMPLATE,
    REVIEW_SUMMARY_HEADER_TEMPLATE,
    REVIEW_SUMMARY_ITEM_TEMPLATE,
    REVIEW_CONFIRMATION_TEMPLATE,
    REVIEW_CONFIRMATION_DEFAULT_INSTRUCTIONS,
    REVIEW_CONFIRMATION_DEFAULT_PROMPT,
    REVIEW_UNCLEAR_EDIT_DIRECTIVE_TEMPLATE,
    REVIEW_UNCLEAR_GENERAL_DIRECTIVE_TEMPLATE,
    COMPLETION_MESSAGE_TEMPLATE,
    CANCELLATION_MESSAGE_TEMPLATE,
    ACTIVE_EVENT_MESSAGE_TEMPLATE,
    REVIEW_EVENT_MESSAGE_TEMPLATE,
    COMPLETION_EVENT_MESSAGE_TEMPLATE,
    CANCELLATION_EVENT_MESSAGE_TEMPLATE,
    QUESTION_DIRECTIVE_TEMPLATE,
    INTERVIEW_PROMPT_TEMPLATE,
)

if TYPE_CHECKING:
    from jvagent.action.interview.core.interview_session import InterviewSession
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)

# Module-level registry for completion handlers (keyed by interview_type)
# This is populated when @on_interview_complete decorated functions are defined
_completion_handlers: Dict[str, Callable] = {}

# Module-level registry for input validators and handlers
# Keyed by (interview_type, question_name) -> function
# This is populated when @input_validator or @input_handler decorated functions are defined
# Format: {(interview_type, question_name): function}
_input_validator_registry: Dict[Tuple[str, str], Callable] = {}
_input_handler_registry: Dict[Tuple[str, str], Callable] = {}


@dataclass
class ClassificationResult:
    """Result of unified classification and extraction routine.
    
    Uses unified intent types: CANCELLATION, CONFIRMATION, UPDATE, SUBMISSION, NONE
    """
    intent: str  # "CANCELLATION", "CONFIRMATION", "UPDATE", "SUBMISSION", "NONE"
    confidence: float = 1.0  # Confidence score for the classification
    
    # Unified field/value structure (used for both UPDATE and SUBMISSION)
    field: Optional[str] = None  # Field name (for UPDATE intent) or null
    value: Optional[Any] = None  # Field value (for UPDATE intent) or null
    
    # For SUBMISSION intent - extracted field values (multiple fields)
    extracted_data: Optional[Dict[str, Any]] = None  # Extracted responses for "SUBMISSION" intent




class InterviewInteractAction(InteractAction, ABC):
    """Unified interview system orchestrator.
    
    This action manages the complete interview lifecycle:
    1. Creates and chains QuestionNode instances from question_index
    2. Manages InterviewSession state (ACTIVE, REVIEW, COMPLETED, CANCELLED)
    3. Uses unified classification to detect intent and extract field values
    4. Generates appropriate directives based on state and classification results
    5. Handles state transitions within the same interaction when appropriate
    
    The system uses a single unified prompt that accepts both utterance and
    interpretation (when available) to detect intent and extract information
    in one LLM call.
    
    Attributes:
        question_index: List of question configurations defining the interview schema
        
    Decorator Support:
        Use @input_handler('question_name') and @input_validator('question_name') decorators
        to register handlers and validators instead of embedding them in question_index.
        Use @on_interview_complete('InterviewType') to register completion handlers.
    """
    
    description: str = "Unified orchestrator for interview system"
    
    # Class-level registries for decorator-registered handlers and validators
    # These are populated when the class is defined via decorators
    _input_handlers: Dict[str, Callable] = {}
    _input_validators: Dict[str, Callable] = {}
    
    def __init_subclass__(cls, **kwargs):
        """Initialize subclass and collect decorator-registered handlers/validators."""
        super().__init_subclass__(**kwargs)
        
        # Initialize class-level registries
        cls._input_handlers = {}
        cls._input_validators = {}
        
        # Load validators/handlers from module-level registry for this class
        class_name = cls.__name__
        for (interview_type, question_name), func in _input_validator_registry.items():
            if interview_type == class_name:
                cls._input_validators[question_name] = func
        
        for (interview_type, question_name), func in _input_handler_registry.items():
            if interview_type == class_name:
                cls._input_handlers[question_name] = func
        
        # Also scan class attributes for decorated functions (class methods)
        for attr_name in dir(cls):
            attr = getattr(cls, attr_name, None)
            if callable(attr) and hasattr(attr, '_interview_question_name'):
                question_name = attr._interview_question_name
                handler_type = getattr(attr, '_interview_handler_type', None)
                
                if handler_type == "input_handler":
                    cls._input_handlers[question_name] = attr
                elif handler_type == "input_validator":
                    cls._input_validators[question_name] = attr
    
    @staticmethod
    def get_completion_handler(interview_type: str) -> Optional[Callable]:
        """Get completion handler for an interview type.
        
        Args:
            interview_type: Class name of the InterviewInteractAction
            
        Returns:
            Completion handler function if found, None otherwise
        """
        return _completion_handlers.get(interview_type)
    
    @classmethod
    def get_input_handler(cls, question_name: str) -> Optional[Callable]:
        """Get input handler for a question by name (from decorator registry).
        
        Args:
            question_name: Name of the question
            
        Returns:
            Input handler function if found, None otherwise
        """
        return cls._input_handlers.get(question_name)
    
    @classmethod
    def get_input_validator(cls, question_name: str) -> Optional[Callable]:
        """Get input validator for a question by name (from decorator registry).
        
        Checks both class-level registry and module-level registry.
        
        Args:
            question_name: Name of the question
            
        Returns:
            Input validator function if found, None otherwise
        """
        # First check class-level registry
        validator = cls._input_validators.get(question_name)
        
        # If not found, check module-level registry (in case it was registered after class definition)
        if not validator:
            validator = _input_validator_registry.get((cls.__name__, question_name))
            if validator:
                # Cache it in class registry for future lookups
                cls._input_validators[question_name] = validator
        
        return validator
    
    weight: int = attribute(
        default=-40,
        description="Execution weight (runs after InteractRouter but before PersonaAction)",
    )
    
    always_execute: bool = attribute(
        default=False,
        description="Only execute when interview should be active",
    )
    
    question_index: List[Dict[str, Any]] = attribute(
        default_factory=list,
        description="List of question configurations defining the interview schema. Can be overridden in agent.yaml",
    )
    
    anchors: List[str] = attribute(
        default_factory=list,
        description=(
            "Anchor statements for InteractRouter routing. REQUIRED when using InteractRouter. "
            "Must include anchors for both initial entry (starting the interview) and intermediate states "
            "(when questions are being answered). The action's class name is automatically used as the key "
            "when collected by InteractRouter."
        ),
    )
    
    # Model Configuration
    model_action_type: str = attribute(
        default="OpenAILanguageModelAction",
        description="Entity type of the LanguageModelAction to use",
    )
    
    model: str = attribute(
        default="gpt-4o-mini", 
        description="Default model name"
    )
    
    model_temperature: float = attribute(
        default=0.1, 
        description="Temperature for LLM generation"
    )
    
    model_max_tokens: int = attribute(
        default=4096, 
        description="Max tokens for LLM generation"
    )
    
    use_history: bool = attribute(
        default=True, 
        description="Use conversation history for LLM generation"
    )
    
    max_statement_length: int = attribute(
        default=400, 
        description="Max length of statement to include in history"
    )
    
    history_limit: int = attribute(
        default=5, 
        description="Max number of statements to include in history"
    )
    
    # Summary formatting templates (for REVIEW state)
    summary_header_template: str = attribute(
        default=REVIEW_SUMMARY_HEADER_TEMPLATE,
        description="Template for the summary header. Defaults to REVIEW_SUMMARY_HEADER_TEMPLATE from prompts.py",
    )
    
    summary_item_template: str = attribute(
        default=REVIEW_SUMMARY_ITEM_TEMPLATE,
        description="Template for each summary item. Use {display_name} and {value} placeholders. Defaults to REVIEW_SUMMARY_ITEM_TEMPLATE from prompts.py",
    )
    
    # Confirmation directive template (for REVIEW state)
    # Consolidated template with placeholders: {summary}, {instructions}, {prompt}
    confirmation_template: str = attribute(
        default=REVIEW_CONFIRMATION_TEMPLATE,
        description="Consolidated template for review confirmation. Use {summary}, {instructions}, and {prompt} placeholders. Defaults to REVIEW_CONFIRMATION_TEMPLATE from prompts.py",
    )
    
    # Default values for consolidated template placeholders (can be customized)
    confirmation_instructions: str = attribute(
        default=REVIEW_CONFIRMATION_DEFAULT_INSTRUCTIONS,
        description="Default instructions text for review confirmation. Used in {instructions} placeholder. Defaults to REVIEW_CONFIRMATION_DEFAULT_INSTRUCTIONS from prompts.py",
    )
    
    confirmation_prompt: str = attribute(
        default=REVIEW_CONFIRMATION_DEFAULT_PROMPT,
        description="Default prompt text for review confirmation. Used in {prompt} placeholder. Defaults to REVIEW_CONFIRMATION_DEFAULT_PROMPT from prompts.py",
    )
    
    # Unclear response directive templates (for REVIEW state)
    unclear_edit_directive_template: str = attribute(
        default=REVIEW_UNCLEAR_EDIT_DIRECTIVE_TEMPLATE,
        description="Template for unclear edit intent. Use {field_list} placeholder. Defaults to REVIEW_UNCLEAR_EDIT_DIRECTIVE_TEMPLATE from prompts.py",
    )
    
    unclear_general_directive_template: str = attribute(
        default=REVIEW_UNCLEAR_GENERAL_DIRECTIVE_TEMPLATE,
        description="Template for general unclear responses. Defaults to REVIEW_UNCLEAR_GENERAL_DIRECTIVE_TEMPLATE from prompts.py",
    )
    
    # Interview prompt template
    interview_prompt: str = attribute(
        default=INTERVIEW_PROMPT_TEMPLATE,
        description="Interview prompt template that combines intent detection (CANCELLATION, CONFIRMATION, UPDATE, SUBMISSION) with response extraction in a single LLM call. Defaults to INTERVIEW_PROMPT_TEMPLATE from prompts.py",
    )
    
    # Update prompt template (for prompting user for new value when updating)
    update_prompt_for_value_template: str = attribute(
        default=UPDATE_PROMPT_FOR_VALUE_TEMPLATE,
        description="Template for prompting user for new value when updating a field. Use {field_display} and {current_value} placeholders. Defaults to UPDATE_PROMPT_FOR_VALUE_TEMPLATE from prompts.py",
    )
    
    # Completion message template (for COMPLETED state)
    completion_message_template: str = attribute(
        default=COMPLETION_MESSAGE_TEMPLATE,
        description="Message template shown when interview is completed (if no completion handler is registered). Defaults to COMPLETION_MESSAGE_TEMPLATE from prompts.py",
    )
    
    # Cancellation message template (for CANCELLED state)
    cancellation_message_template: str = attribute(
        default=CANCELLATION_MESSAGE_TEMPLATE,
        description="Message template shown when interview is cancelled. Defaults to CANCELLATION_MESSAGE_TEMPLATE from prompts.py",
    )
    
    # Active event message template (for ACTIVE state)
    active_event_message_template: str = attribute(
        default=ACTIVE_EVENT_MESSAGE_TEMPLATE,
        description="Event message template for active interview state. Use {class_name} placeholder. Defaults to ACTIVE_EVENT_MESSAGE_TEMPLATE from prompts.py",
    )
    
    # Review event message template (for REVIEW state)
    review_event_message_template: str = attribute(
        default=REVIEW_EVENT_MESSAGE_TEMPLATE,
        description="Event message template for review interview state. Use {class_name} placeholder. Defaults to REVIEW_EVENT_MESSAGE_TEMPLATE from prompts.py",
    )
    
    # Completion event message template (for COMPLETED state)
    completion_event_message_template: str = attribute(
        default=COMPLETION_EVENT_MESSAGE_TEMPLATE,
        description="Event message template for completed interview state. Documents that the interview process has been completed. Use {class_name} placeholder. Defaults to COMPLETION_EVENT_MESSAGE_TEMPLATE from prompts.py",
    )
    
    # Cancellation event message template (for CANCELLED state)
    cancellation_event_message_template: str = attribute(
        default=CANCELLATION_EVENT_MESSAGE_TEMPLATE,
        description="Event message template for cancelled interview state. Documents that the interview process has been cancelled. Use {class_name} placeholder. Defaults to CANCELLATION_EVENT_MESSAGE_TEMPLATE from prompts.py",
    )
    
    # Question directive template (for ACTIVE state - question prompting)
    question_directive_template: str = attribute(
        default=QUESTION_DIRECTIVE_TEMPLATE,
        description="Consolidated template for formatting question directives. Uses {question}, {description}, and {instructions} placeholders. Instructions are optional and only included if provided. Defaults to QUESTION_DIRECTIVE_TEMPLATE from prompts.py",
    )

    async def _generate_directive(
        self,
        session: InterviewSession,
        classification_result: ClassificationResult,
        visitor: "InteractWalker",
        interaction: Interaction
    ) -> None:
        """Generate and send directive based on session state and classification result.
        
        Flow:
        1. Handle high-priority intents (CANCELLATION, CONFIRMATION) that cause state transitions
        2. Route to state-specific handlers based on current session state
        3. Handle cascading state transitions (e.g., ACTIVE -> REVIEW -> COMPLETED in same turn)
        
        Args:
            session: Interview session
            classification_result: Result from classification routine
            visitor: InteractWalker
            interaction: Current interaction
        """
        # ========================================================================
        # STEP 1: Handle high-priority intents that cause immediate state transitions
        # ========================================================================
        
        # CANCELLATION: Highest priority - can occur in any state
        if classification_result.intent == "CANCELLATION" and session.state != InterviewState.CANCELLED:
            try:
                session.transition_to(InterviewState.CANCELLED)
                await session.save()
                # Fall through to state-based routing (CANCELLED branch will handle it)
            except Exception as e:
                logger.error(f"{self.get_class_name()}: Failed to transition to CANCELLED: {e}", exc_info=True)
        
        # CONFIRMATION: Only valid in REVIEW state - transition to COMPLETED immediately
        if classification_result.intent == "CONFIRMATION" and session.state == InterviewState.REVIEW:
            try:
                session.transition_to(InterviewState.COMPLETED)
                await session.save()
                await self._generate_completed_directive(session, visitor)
                return  # Exit early - completion handled in same turn
            except Exception as e:
                logger.error(f"{self.get_class_name()}: Failed to transition to COMPLETED: {e}", exc_info=True)
        
        # ========================================================================
        # STEP 2: Route to state-specific handlers
        # ========================================================================
        
        if session.state == InterviewState.ACTIVE:
            await self._generate_active_directive(session, classification_result, visitor, interaction)
            # Handle cascading transition: ACTIVE -> REVIEW (when all questions answered)
            if session.state == InterviewState.REVIEW:
                await self._generate_review_directive(session, classification_result, visitor)
                # Handle cascading transition: REVIEW -> COMPLETED (if CONFIRMATION was missed)
                # Note: This should not occur as CONFIRMATION is handled above, but kept as safeguard
                if session.state == InterviewState.COMPLETED:
                    await self._generate_completed_directive(session, visitor)
        
        elif session.state == InterviewState.REVIEW:
            # Handle REVIEW state (UPDATE intent, unclear responses, first-time summary display)
            # Note: CONFIRMATION intent is handled above and returns early
            await self._generate_review_directive(session, classification_result, visitor)
        
        elif session.state == InterviewState.COMPLETED:
            # Handle already-completed state (e.g., from previous interaction)
            await self._generate_completed_directive(session, visitor)
        
        elif session.state == InterviewState.CANCELLED:
            # Handle cancelled state (cleanup and message)
            await self._generate_cancelled_directive(session, visitor)
        
        else:
            logger.warning(f"{self.get_class_name()}: Unknown session state: {session.state}")
    
    async def _generate_active_directive(
        self,
        session: InterviewSession,
        classification_result: ClassificationResult,
        visitor: "InteractWalker",
        interaction: Interaction
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
        if classification_result.intent == "UPDATE":
            # Check if field needs clarification
            if not classification_result.field:
                # Ask which field to update
                answered_fields = session.get_answered_questions()
                field_list = ", ".join([f.replace("_", " ") for f in answered_fields])
                directive = self.unclear_edit_directive_template.format(field_list=field_list)
                await self._respond_with_directive(visitor, directive, self.active_event_message_template.format(class_name=self.get_class_name()))
                return
            
            # Handle the update inline (reusing QuestionNode processing/validation)
            update_completed = await self._handle_update_inline(
                classification_result,
                session,
                visitor,
                interaction
            )
            
            if not update_completed:
                # Waiting for value or clarification
                return
            
            updated_field = classification_result.field
            
            # Check if all questions answered after update
            if session.has_all_required_answers():
                session.active_question_key = None
                session.transition_to(InterviewState.REVIEW)
                await session.save()
                # State will be checked in _generate_directive to generate review directive
                return
        
        # Handle response extraction
        elif classification_result.intent == "SUBMISSION" and classification_result.extracted_data:
            # Validate and store responses
            question_walker = QuestionWalker()
            question_walker.interview_session = session
            question_walker.interaction = interaction
            question_walker.question_directive_template = self.question_directive_template
            
            await self._process_responses_to_questions(
                classification_result.extracted_data,
                session,
                visitor,
                interaction,
                question_walker
            )
            
            # If active_question_key is set to an unanswered field (invalid response), return
            if session.active_question_key and session.active_question_key in session.get_unanswered_questions():
                return
        
        # Check if all required questions are answered
        if session.has_all_required_answers():
            session.active_question_key = None
            session.transition_to(InterviewState.REVIEW)
            await session.save()
            # State will be checked in _generate_directive to generate review directive
            return
        
        # Get directive for next question using QuestionWalker
        question_walker = QuestionWalker()
        question_walker.interview_session = session
        question_walker.interaction = interaction
        question_walker.question_directive_template = self.question_directive_template
        
        unanswered = session.get_unanswered_questions()
        if not unanswered:
            # All questions answered, transition to REVIEW
            session.active_question_key = None
            session.transition_to(InterviewState.REVIEW)
            await session.save()
            # State will be checked in _generate_directive to generate review directive
            return
        
        start_from = session.active_question_key if session.active_question_key in unanswered else None
        question_node = await question_walker.find_next_question(session, interview_action=self, start_from=start_from)
        
        if question_node:
            directive = await question_node.execute(question_walker)
            if directive:
                # If an update was just handled, prepend a brief confirmation
                if updated_field:
                    field_display = updated_field.replace("_", " ").title()
                    new_value = session.get_response(updated_field)
                    confirmation = f"Tell the user: Updated {field_display} to {new_value}. "
                    directive = confirmation + directive
                await self._respond_with_directive(visitor, directive, self.active_event_message_template.format(class_name=self.get_class_name()))
    
    async def _generate_review_directive(
        self,
        session: InterviewSession,
        classification_result: ClassificationResult,
        visitor: "InteractWalker"
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
        # Note: CONFIRMATION intent is handled at the top level of _generate_directive
        # to ensure state transition and completion happen in the same turn
        # This check remains as a safeguard but should not be reached if CONFIRMATION was detected
        if classification_result.intent == "CONFIRMATION":
            # This should have been handled above, but if we reach here, handle it
            logger.warning(f"{self.get_class_name()}: CONFIRMATION intent reached _generate_review_directive, should have been handled earlier")
            session.transition_to(InterviewState.COMPLETED)
            await session.save()
            await self._generate_completed_directive(session, visitor)
            return
        
        # Handle update
        if classification_result.intent == "UPDATE":
            if not classification_result.field:
                # Ask which field to update
                answered_fields = session.get_answered_questions()
                field_list = ", ".join([f.replace("_", " ") for f in answered_fields])
                directive = self.unclear_edit_directive_template.format(field_list=field_list)
                await self._respond_with_directive(visitor, directive, self.review_event_message_template.format(class_name=self.get_class_name()))
                return
            
            # Handle the update inline (reusing QuestionNode processing/validation)
            interaction = visitor.interaction
            update_completed = await self._handle_update_inline(
                classification_result,
                session,
                visitor,
                interaction
            )
            
            if update_completed:
                # Show updated summary immediately in same turn
                directive = self._build_confirmation_directive(session)
                await self._respond_with_directive(visitor, directive, self.review_event_message_template.format(class_name=self.get_class_name()))
            return
        
        # Handle unclear response (NONE intent or other)
        if classification_result.intent == "NONE" or not classification_result.intent:
            directive = self.unclear_general_directive_template
            await self._respond_with_directive(visitor, directive, self.review_event_message_template.format(class_name=self.get_class_name()))
            return
        
        # Default: Show summary for review (first entry to REVIEW state)
        directive = self._build_confirmation_directive(session)
        await self._respond_with_directive(visitor, directive, self.review_event_message_template.format(class_name=self.get_class_name()))
    
    async def _generate_completed_directive(
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
        # Get completion handler for this interview type
        interview_type = session.interview_type
        completion_handler = self.get_completion_handler(interview_type)
        
        if completion_handler:
            try:
                await completion_handler(session, visitor, self)
                # Completion handler is responsible for sending its own message if needed
            except Exception as e:
                logger.error(f"{self.get_class_name()}: Completion handler failed: {e}", exc_info=True)
                # Send generic completion message on error
                await self._respond_with_directive(
                    visitor,
                    self.completion_message_template,
                    self.completion_event_message_template.format(class_name=self.get_class_name())
                )
        else:
            # No completion handler registered, send generic message
            await self._respond_with_directive(
                visitor,
                self.completion_message_template,
                self.completion_event_message_template.format(class_name=self.get_class_name())
            )
        
        # Clean up and remove the session (always, regardless of handler success/failure)
        try:
            await session.cleanup()
            # Clear session reference from visitor
            visitor.interview_session = None
        except Exception as e:
            logger.error(f"{self.get_class_name()}: Failed to cleanup completed session: {e}", exc_info=True)
    
    async def _generate_cancelled_directive(
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
        await self._respond_with_directive(
            visitor,
            self.cancellation_message_template,
            self.cancellation_event_message_template.format(class_name=self.get_class_name())
        )
        
        # Clean up and remove the session
        try:
            await session.cleanup()
            # Clear session reference from visitor
            visitor.interview_session = None
        except Exception as e:
            logger.error(f"{self.get_class_name()}: Failed to cleanup cancelled session: {e}", exc_info=True)
    
    async def _handle_update_inline(
        self,
        classification_result: ClassificationResult,
        session: InterviewSession,
        visitor: "InteractWalker",
        interaction: Interaction
    ) -> bool:
        """Handle update inline using QuestionNode processing/validation.
        
        Reuses the same QuestionNode flow as SUBMISSION for consistency.
        
        Args:
            classification_result: Classification result with UPDATE intent
            session: Interview session
            visitor: InteractWalker
            interaction: Current interaction
            
        Returns:
            True if update completed, False if prompting for value or validation failed
        """
        field = classification_result.field
        if not field:
            logger.warning(f"{self.get_class_name()}: Update field is None")
            return False
        
        # Get question node
        question_node = await self._get_question_node(field, session)
        if not question_node:
            logger.warning(f"{self.get_class_name()}: Question node not found for {field}")
            return False
        
        # If value is missing, prompt for it
        if classification_result.value is None:
            current_value = session.get_response(field)
            field_display = field.replace("_", " ").title()
            
            directive = self.update_prompt_for_value_template.format(
                field_display=field_display,
                current_value=current_value
            )
            
            await self._respond_with_directive(visitor, directive, self.active_event_message_template.format(class_name=self.get_class_name()))
            return False  # Update not completed, waiting for value
        
        # Process and validate the new value (same flow as SUBMISSION)
        new_value = classification_result.value
        
        # Process input first (handles custom transformations like autocorrection)
        if isinstance(new_value, str):
            new_value = await question_node.process_input(new_value, session, interaction)
        
        # Validate the processed value
        validation_status, feedback, corrected_value = await question_node.validate_response(new_value, session)
        
        # Use corrected value if validator provided one
        if corrected_value is not None:
            new_value = corrected_value
        session.set_validation_status(field, validation_status)
        
        if validation_status == ValidationStatus.VALID:
            # Update session with audit trail
            old_value = session.get_response(field)
            session.update_response(field, new_value, old_value)
            await session.save()
            logger.debug(f"{self.get_class_name()}: Updated {field} to {new_value}")
            return True  # Update completed
            
        elif validation_status == ValidationStatus.VALID_WITH_FLAG:
            # Store but ask for clarification
            old_value = session.get_response(field)
            session.update_response(field, new_value, old_value)
            await session.save()
            
            if feedback:
                # Ensure proper prefix if not already present
                feedback_msg = feedback
                if not feedback_msg.startswith("Tell the user:") and not feedback_msg.startswith("Ask:"):
                    feedback_msg = f"Ask: {feedback_msg}"
                await self._respond_with_directive(visitor, feedback_msg, self.active_event_message_template.format(class_name=self.get_class_name()))
            return True  # Update stored, clarification requested
            
        else:  # INVALID
            # Don't store, ask for correction
            error_msg = feedback or f"Tell the user: Please provide a valid value for {field}."
            if not error_msg.startswith("Tell the user:") and not error_msg.startswith("Ask:"):
                error_msg = f"Tell the user: {error_msg}"
            await self._respond_with_directive(visitor, error_msg, self.active_event_message_template.format(class_name=self.get_class_name()))
            return False  # Update failed, waiting for valid value
    
    async def _process_responses_to_questions(
        self,
        responses: Dict[str, Any],
        session: InterviewSession,
        visitor: "InteractWalker",
        interaction: Interaction,
        question_walker: QuestionWalker
    ) -> None:
        """Process and validate extracted responses using QuestionWalker.
        
        Only stores responses if they pass validation. Invalid responses trigger
        a directive with feedback instead of being stored.
        
        Args:
            responses: Extracted responses dictionary
            session: Interview session
            visitor: InteractWalker
            interaction: Current interaction
            question_walker: QuestionWalker instance
        """
        for field, value in responses.items():
            # Find question node for validation
            question_node = await self._get_question_node(field, session)
            if not question_node:
                logger.warning(f"{self.get_class_name()}: Question node not found for {field}")
                continue
            
            # Use QuestionWalker to process and validate
            # Returns (final_value, validation_status, feedback) where final_value may be autocorrected
            final_value, validation_status, feedback = await question_walker.process_and_validate(
                value,
                question_node,
                session,
                interaction
            )
            session.set_validation_status(field, validation_status)
            
            # Check if there's a custom validator registered for this field
            has_custom_validator = False
            question_name = question_node.state.get("name", "")
            if question_name and session:
                action_class = question_node._get_action_class_from_session(session)
                if action_class:
                    validator = action_class.get_input_validator(question_name)
                    if validator:
                        has_custom_validator = True
            
            # Also check for string reference validator
            constraints = question_node.state.get("constraints", {})
            if not has_custom_validator and constraints.get("input_validator"):
                has_custom_validator = True
            
            if validation_status == ValidationStatus.VALID:
                # Store final value (may be autocorrected) - only if valid
                session.set_response(field, final_value)
                # Clear active_question_key so traversal finds next unanswered question
                session.active_question_key = None
                await session.save()
            
            elif validation_status == ValidationStatus.VALID_WITH_FLAG:
                # Store final value (may be autocorrected) but ask for clarification
                session.set_response(field, final_value)
                # Clear active_question_key so traversal finds next unanswered question
                session.active_question_key = None
                await session.save()
                
                # Ask clarifying question
                if feedback:
                    # Use feedback message as-is without prepending
                    feedback_msg = feedback
                    await self._respond_with_directive(visitor, feedback_msg, self.active_event_message_template.format(class_name=self.get_class_name()))
            
            else:  # INVALID
                # CRITICAL: Do NOT store invalid values, especially if there's a custom validator
                # Keep active_question_key pointing to this field so we can re-ask
                session.active_question_key = field
                await session.save()
                
                # Generate directive with validation feedback
                if has_custom_validator and feedback:
                    # Use validator's feedback message as-is without prepending
                    error_msg = feedback
                else:
                    # Fallback to generic message
                    error_msg = feedback or f"Tell the user: Please provide a valid value for {field}."
                
                await self._respond_with_directive(visitor, error_msg, self.active_event_message_template.format(class_name=self.get_class_name()))
                break  # Stop processing further responses, focus on correcting this field
    
    async def _get_question_node(
        self, 
        field: str, 
        session: InterviewSession
    ) -> Optional[QuestionNode]:
        """Get QuestionNode for a specific field.
        
        Args:
            field: Field name
            session: Interview session
            
        Returns:
            QuestionNode if found, None otherwise
        """
        # Find question config
        question_config = session.get_question_by_name(field)
        if not question_config:
            return None
        
        # Question nodes are connected directly to InterviewInteractAction
        question_nodes = await self.nodes(direction="out", node=QuestionNode)
        question_node = next(
            (n for n in question_nodes if n.label == field),
            None
        )
        
        if not question_node:
            # Create on-demand if not found (shouldn't happen in normal flow)
            question_node = await QuestionNode.create(
                agent_id=self.agent_id,
                state=question_config,
                label=field,
            )
            await self.connect(question_node)
        
        return question_node
    
    def _format_summary(self, session: InterviewSession) -> str:
        """Format collected responses as a summary.
        
        Args:
            session: Interview session
            
        Returns:
            Formatted summary string
        """
        lines = [self.summary_header_template]
        
        for question_config in session.question_index:
            field_name = question_config.get("name", "")
            if not field_name:
                continue
            
            value = session.get_response(field_name)
            if value is None:
                continue
            
            # Format field name nicely
            display_name = field_name.replace("_", " ").title()
            item = self.summary_item_template.format(
                display_name=display_name,
                value=value
            )
            lines.append(item)
        
        return "\n".join(lines)
    
    def _build_confirmation_directive(self, session: InterviewSession) -> str:
        """Build the complete confirmation directive from consolidated template.
        
        Args:
            session: Interview session
            
        Returns:
            Complete confirmation directive string
        """
        summary = self._format_summary(session)
        
        # Use consolidated template with all subparts as placeholders
        return self.confirmation_template.format(
            summary=summary,
            instructions=self.confirmation_instructions,
            prompt=self.confirmation_prompt,
        )
    
    async def _respond_with_directive(
        self,
        visitor: "InteractWalker",
        directive: str,
        event_name: str
    ) -> None:
        """Respond with a directive.
        
        Args:
            visitor: InteractWalker
            directive: Directive string
            event_name: Event name to add
        """
        if directive:
            await visitor.add_event(event_name)
            await self.respond(
                visitor,
                directives=[directive],
                parameters=self.parameters if self.parameters else None
            )
    
    async def _classify_and_extract(
        self,
        session: InterviewSession,
        utterance: str,
        interaction: Interaction,
        visitor: "InteractWalker"
    ) -> ClassificationResult:
        """Unified classification and extraction routine.
        
        Uses a single LLM call to detect intent (CANCELLATION, CONFIRMATION, UPDATE, SUBMISSION, NONE)
        and extract field values simultaneously for efficiency and consistency.
        
        This runs in parallel with directive generation to maintain InterviewSession
        state and data while determining the best directive to drive the conversation.
        
        Args:
            session: Interview session
            utterance: User's utterance (fallback if interpretation not available)
            interaction: Current interaction
            visitor: InteractWalker
            
        Returns:
            ClassificationResult with unified intent and extracted data
        """
        # Build user message with both utterance and interpretation (if available)
        user_message_parts = []
        if utterance and utterance.strip():
            user_message_parts.append(f"User's utterance: {utterance}")
        if interaction.interpretation and interaction.interpretation.strip():
            user_message_parts.append(f"Interpretation: {interaction.interpretation}")
        
        if not user_message_parts:
            return ClassificationResult(intent="NONE")
        
        user_message = "\n".join(user_message_parts)
        
        # Skip classification for terminal states
        if session.state == InterviewState.COMPLETED or session.state == InterviewState.CANCELLED:
            return ClassificationResult(intent="NONE")
        
        # Unified classification and extraction using single prompt
        try:
            # Build context for unified prompt
            current_state = session.state.value
            
            # Calculate progress
            answered = len(session.get_answered_questions())
            total = len(session.question_index)
            progress_info = f"{answered}/{total} questions answered"
            
            # Format answered fields with values
            answered_fields = session.get_answered_questions()
            answered_fields_with_values = []
            for field in answered_fields:
                value = session.get_response(field)
                question_config = session.get_question_by_name(field)
                description = ""
                if question_config:
                    constraints = question_config.get("constraints", {})
                    description = constraints.get("description", "")
                    if not description:
                        description = field.replace("_", " ").title()
                answered_fields_with_values.append(f"- {field} ({description}): {value}")
            
            # Get unanswered questions for extraction
            unanswered = session.get_unanswered_questions()
            if session.active_question_key and session.active_question_key in unanswered:
                # Focus on the active question (revision or re-prompt for invalid)
                active_questions = [q for q in session.question_index if q.get("name") == session.active_question_key]
            else:
                active_questions = [q for q in session.question_index if q.get("name") in unanswered]
            
            # Build entities list for extraction
            entities_list = []
            
            for item in active_questions:
                key = item.get('name')
                constraints = item.get('constraints', {})
                if not key or not constraints:
                    continue
                desc = constraints.get('description', '')
                other_constraints = {k: v for k, v in constraints.items() if k != 'description'}
                constraint_strs = [f"{k}: {v}" for k, v in other_constraints.items()]
                constraint_part = f" ({', '.join(constraint_strs)})" if constraint_strs else ""
                entities_list.append(f"- {key}: {desc}{constraint_part}")
            
            entities_to_extract = "\n".join(entities_list) if entities_list else "None (all questions answered)"
            
            # Build unified prompt
            prompt = self.interview_prompt.format(
                user_message=user_message,
                current_state=current_state,
                progress_info=progress_info,
                answered_fields_with_values="\n".join(answered_fields_with_values) if answered_fields_with_values else "None",
                entities_to_extract=entities_to_extract
            )
            
            # Get model action
            model_action = await self.get_model_action(required=True)
            if not model_action:
                logger.warning(f"{self.get_class_name()}: Could not get model action for unified classification")
                return ClassificationResult(intent="NONE")
            
            # Get conversation history if needed
            conversation_history = None
            if self.use_history:
                conversation_history = await self._get_conversation_history(
                    interaction,
                    self.history_limit,
                    with_utterance=True,
                    with_response=False,
                    with_interpretation=False,
                    with_event=True,
                    max_statement_length=self.max_statement_length,
                )
            
            # Call LLM with unified prompt
            # Use the primary text (interpretation if available, otherwise utterance) as the prompt
            primary_text = interaction.interpretation if interaction.interpretation else utterance
            response = await model_action.generate(
                prompt=primary_text,
                stream=False,
                system=prompt,
                history=conversation_history,
                calling_action_name=self.get_class_name(),
                model=self.model,
                temperature=self.model_temperature,
                max_tokens=self.model_max_tokens,
                response_format={"type": "json_object"},
            )
            
            # Parse JSON response
            if isinstance(response, str):
                result = self._extract_json(response)
            else:
                result = response
            
            if not result:
                return ClassificationResult(intent="NONE")
            
            # Extract intent
            intent = result.get("intent", "NONE").upper()
            confidence = result.get("confidence", 1.0)
            
            # Build ClassificationResult
            classification_result = ClassificationResult(
                intent=intent,
                confidence=confidence,
                field=result.get("field"),
                value=result.get("value")
            )
            
            # Handle SUBMISSION intent - extract field values
            if intent == "SUBMISSION":
                # Extract field values (exclude intent-related keys)
                intent_keys = {"intent", "confidence", "field", "value"}
                extracted_data = {k: v for k, v in result.items() if k not in intent_keys}
                
                # Filter out empty/None/whitespace-only values
                filtered_data = {}
                for field, value in extracted_data.items():
                    if value is not None and isinstance(value, str) and value.strip():
                        filtered_data[field] = value
                    elif value is not None and not isinstance(value, str):
                        filtered_data[field] = value
                
                if filtered_data:
                    classification_result.extracted_data = filtered_data
            
            return classification_result
            
        except json.JSONDecodeError as e:
            logger.error(f"{self.get_class_name()}: Failed to parse unified classification JSON: {e}", exc_info=True)
            return ClassificationResult(intent="NONE")
        except Exception as e:
            logger.error(f"{self.get_class_name()}: Failed to classify/extract via unified prompt: {e}", exc_info=True)
            return ClassificationResult(intent="NONE")
    

    async def on_register(self) -> None:
        """Register the action and build question nodes.
        
        Note: Errors are automatically logged by the base Action class.
        """
        
        # Validate question_index is defined
        if not self.question_index:
            logger.warning(f"{self.get_class_name()}: question_index is empty. Define questions in subclass or agent.yaml")
        
        # Build QuestionNode chain
        await self._build_question_nodes()

    async def on_reload(self) -> None:
        """Reload the action - rebuild question nodes if question_index changed."""
        
        # Get current question node labels to detect changes
        existing_nodes = await self.nodes(direction="out", node=QuestionNode)
        existing_labels = {n.label for n in existing_nodes}
        
        # Get expected labels from question_index
        expected_labels = {q.get("name", "") for q in self.question_index if q.get("name")}
        
        # If labels changed, rebuild question nodes
        if existing_labels != expected_labels:
            # Disconnect and delete old question nodes
            for node in existing_nodes:
                await self.disconnect(node)
                await node.delete()
            # Rebuild
            await self._build_question_nodes()
    
    async def _build_question_nodes(self) -> None:
        """Build QuestionNode tree from question_index with conditional branches.
        
        Creates QuestionNodes and connects them based on branches configuration.
        Supports both linear (no branches) and tree-based (with branches) arrangements.
        """
        from .core.question_edge import QuestionEdge
        
        # Create all question nodes first
        question_node_map = {}
        for question_config in self.question_index:
            question_name = question_config.get("name", "")
            if not question_name:
                continue
            
            question_node = await QuestionNode.create(
                agent_id=self.agent_id,
                state=question_config,
                label=question_name,
            )
            question_node_map[question_name] = question_node
            await self.connect(question_node)
        
        # Now create edges based on branches
        for question_config in self.question_index:
            question_name = question_config.get("name", "")
            if not question_name:
                continue
            
            source_node = question_node_map.get(question_name)
            if not source_node:
                continue
            
            branches = question_config.get("branches", [])
            default_next = question_config.get("default_next")
            
            # Create edges for branches
            if branches:
                for branch in branches:
                    condition = branch.get("condition", {})
                    target_name = branch.get("target")
                    if target_name and target_name in question_node_map:
                        target_node = question_node_map[target_name]
                        # Create edge with condition
                        await source_node.connect(
                            target_node,
                            edge=QuestionEdge,
                            condition=condition
                        )
            elif default_next:
                # Create edge for default_next
                if default_next in question_node_map:
                    target_node = question_node_map[default_next]
                    await source_node.connect(target_node, edge=QuestionEdge)
            else:
                # Linear flow - connect to next question in list
                current_idx = next(
                    (i for i, q in enumerate(self.question_index) if q.get("name") == question_name),
                    -1
                )
                if current_idx >= 0 and current_idx + 1 < len(self.question_index):
                    next_question_name = self.question_index[current_idx + 1].get("name")
                    if next_question_name and next_question_name in question_node_map:
                        target_node = question_node_map[next_question_name]
                        await source_node.connect(target_node, edge=QuestionEdge)
        

    async def execute(self, visitor: "InteractWalker") -> None:
        """Execute interview action using unified classification and directive generation.
        
        Flow:
        1. Load or create session
        2. Check for cancellation (applies to all states)
        3. Classify and extract (parallel routine)
        4. Generate directive based on state and classification
        
        Args:
            visitor: The InteractWalker visiting this action
            
        Note: Errors are automatically logged by InteractWalker.
        """
        interaction = visitor.interaction
        if not interaction:
            logger.warning(f"{self.get_class_name()}: No interaction available")
            return
        
        # Get conversation from interaction
        conversation = await interaction.get_conversation()
        if not conversation:
            logger.warning(f"{self.get_class_name()}: No conversation available")
            return
        
        # Get interview type (class name)
        interview_type = self.get_class_name()
        
        # Query conversation for active session of this interview type
        session = await conversation.node(
            node=[{'InterviewSession': {
                "state": {"$nin": [InterviewState.COMPLETED.value, InterviewState.CANCELLED.value]}
            }}],
            interview_type=interview_type,
        )
        
        # Create new session if none exists
        if not session:
            session = await InterviewSession.create(
                agent_id=self.agent_id,
                conversation_id=conversation.id,
                interview_type=interview_type,
                question_index=self.question_index,
                state=InterviewState.ACTIVE,
            )
            session.started_at = datetime.now()
            await session.save()
            
            # Attach to conversation
            await conversation.connect(session)
        
        # Inject session in visitor for compatibility
        visitor.interview_session = session
        
        # Get utterance
        utterance = visitor.utterance if visitor.utterance else ""
        
        # Unified classification and extraction routine
        classification_result = await self._classify_and_extract(
            session,
            utterance,
            interaction,
            visitor
        )
        
        # Generate directive based on state and classification
        await self._generate_directive(
            session,
            classification_result,
            visitor,
            interaction
        )
    
    async def _get_conversation_history(
        self,
        interaction: Interaction,
        history_limit: int,
        with_utterance: bool = True,
        with_response: bool = False,
        with_interpretation: bool = False,
        with_event: bool = True,
        max_statement_length: Optional[int] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """Get formatted conversation history for the language model.
        
        Args:
            interaction: Current interaction
            history_limit: Number of past interactions to include
            with_utterance: Include user utterances
            with_response: Include AI responses
            with_interpretation: Include interpretations
            with_event: Include events
            max_statement_length: Truncate to this length
            
        Returns:
            List of message dictionaries or None
        """
        if history_limit <= 0:
            return None

        from jvagent.memory.conversation import Conversation

        conversation = await Conversation.get(interaction.conversation_id)
        if not conversation:
            return []

        history = await conversation.get_interaction_history(
            limit=history_limit,
            excluded=interaction.id,
            with_utterance=with_utterance,
            with_response=with_response,
            with_interpretation=with_interpretation,
            with_event=with_event,
            formatted=True,
            max_statement_length=max_statement_length,
        )

        return history if history else []
    
    def _extract_json(self, response: str) -> Dict[str, Any]:
        """Extract JSON from response string.
        
        Args:
            response: Response string
            
        Returns:
            Parsed JSON dictionary
        """
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            # Try to extract JSON from text
            json_match = re.search(r'\{[^{}]*\}', response)
            if json_match:
                try:
                    return json.loads(json_match.group())
                except json.JSONDecodeError:
                    pass
            logger.warning(f"{self.get_class_name()}: Failed to extract JSON from response")
            return {}
    
    async def get_model_action(self, required: bool = False):
        """Get the language model action.
        
        Args:
            required: If True, raises error if action not found
            
        Returns:
            LanguageModelAction instance or None
        """
        try:
            if self.model_action_type:
                model_action = await self.get_action(self.model_action_type)
            else:
                # Fallback to first available LanguageModelAction
                from jvagent.action.model.language.base import LanguageModelAction
                model_action = await self.get_action(LanguageModelAction)
            
            if not model_action and required:
                raise ValueError(f"{self.get_class_name()}: Model action not found (model_action_type={self.model_action_type})")
            
            return model_action
        except Exception as e:
            if required:
                raise
            logger.warning(f"{self.get_class_name()}: Could not get model action: {e}")
            return None


def input_handler(question_name: str):
    """Decorator to register an input handler for a specific question.
    
    Input handlers process raw user input before validation (e.g., normalize time expressions).
    
    The decorator registers the handler in a module-level registry.
    The interview_type is determined from the module where the handler is defined
    by looking for InterviewInteractAction subclasses in that module.
    
    Args:
        question_name: Name of the question (must match 'name' field in question_index)
        
    Handler Signature:
        The handler must accept three parameters:
        - raw_input: str - Raw user input string
        - session: InterviewSession - Interview session for context
        - interaction: Interaction - Interaction node for accessing interaction context
        
    Example:
        @input_handler('available_times')
        def check_training_availability(
            raw_input: str, 
            session: InterviewSession,
            interaction: Interaction
        ) -> str:
            # Process and normalize input
            # Can access interaction.user_id, interaction.utterance, etc.
            return processed_input
    """
    def decorator(func: Callable) -> Callable:
        # Store the question name on the function for later lookup
        func._interview_question_name = question_name  # type: ignore
        func._interview_handler_type = "input_handler"  # type: ignore
        
        # Try to determine the interview_type from the module where this function is defined
        try:
            import inspect
            module = inspect.getmodule(func)
            if module:
                # Look for InterviewInteractAction subclasses in the module
                for name, obj in vars(module).items():
                    if (inspect.isclass(obj) and 
                        issubclass(obj, InterviewInteractAction) and 
                        obj is not InterviewInteractAction):
                        interview_type = obj.__name__
                        # Register in module-level registry
                        _input_handler_registry[(interview_type, question_name)] = func
                        break
                else:
                    logger.warning(f"Could not determine interview_type for handler '{func.__name__}' - it will be registered when the class is defined")
            else:
                logger.warning(f"Could not get module for handler '{func.__name__}'")
        except Exception as e:
            logger.warning(f"Error registering handler '{func.__name__}': {e}")
        
        return func
    return decorator


def input_validator(question_name: str):
    """Decorator to register a validator for a specific question.
    
    Validators validate responses with custom logic.
    
    The decorator registers the validator in a module-level registry.
    The interview_type is determined from the module where the validator is defined
    by looking for InterviewInteractAction subclasses in that module.
    
    Args:
        question_name: Name of the question (must match 'name' field in question_index)
        
    Example:
        @input_validator('user_email')
        def validate_email(value: str, session: InterviewSession) -> Tuple[ValidationStatus, Optional[str]]:
            # Validate email
            return ValidationStatus.VALID, None
    """
    def decorator(func: Callable) -> Callable:
        # Store the question name on the function for later lookup
        func._interview_question_name = question_name  # type: ignore
        func._interview_handler_type = "input_validator"  # type: ignore
        
        # Try to determine the interview_type from the module where this function is defined
        try:
            import inspect
            module = inspect.getmodule(func)
            if module:
                # Look for InterviewInteractAction subclasses in the module
                for name, obj in vars(module).items():
                    if (inspect.isclass(obj) and 
                        issubclass(obj, InterviewInteractAction) and 
                        obj is not InterviewInteractAction):
                        interview_type = obj.__name__
                        # Register in module-level registry
                        _input_validator_registry[(interview_type, question_name)] = func
                        break
                else:
                    logger.warning(f"Could not determine interview_type for validator '{func.__name__}' - it will be registered when the class is defined")
            else:
                logger.warning(f"Could not get module for validator '{func.__name__}'")
        except Exception as e:
            logger.warning(f"Error registering validator '{func.__name__}': {e}")
        
        return func
    return decorator


def on_interview_complete(interview_type: str):
    """Decorator to register a completion handler for a specific interview type.
    
    Completion handlers are called when an interview session reaches the COMPLETED state.
    Use this to process collected data, trigger downstream actions, or perform cleanup.
    
    Args:
        interview_type: Class name of the InterviewInteractAction (e.g., 'SignupInterviewInteractAction')
        
    Handler Signature:
        The handler must accept three parameters:
        - session: InterviewSession - The completed interview session with all collected responses
        - visitor: InteractWalker - The walker for accessing context and responding
        - action: InteractAction - The action instance (use action.respond() to send responses)
        
    Example:
        @on_interview_complete('SignupInterviewInteractAction')
        async def handle_signup_completion(
            session: InterviewSession,
            visitor: InteractWalker,
            action: InteractAction
        ) -> None:
            # Process collected data
            user_name = session.responses.get('user_name')
            user_email = session.responses.get('user_email')
            # Send response using action.respond()
            await action.respond(visitor, directives=["Thank you for signing up!"])
            # Trigger downstream actions, send notifications, etc.
    """
    def decorator(func: Callable) -> Callable:
        # Register the handler in the module-level registry
        _completion_handlers[interview_type] = func
        return func
    return decorator