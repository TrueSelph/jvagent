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

import inspect
import json
import logging
import re
from abc import ABC
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple, Union

from jvagent.action.interact.base import InteractAction
from jvagent.memory import Interaction
from jvspatial.core.annotations import attribute

from .core.interview_service import InterviewService
from .core.interview_session import InterviewSession
from .core.question_node import QuestionNode
from .core.question_walker import QuestionWalker
from .core.enums import InterviewState, ValidationStatus
from .prompts import (
    UPDATE_PROMPT_FOR_VALUE_TEMPLATE,
    REVIEW_SUMMARY_HEADER_TEMPLATE,
    REVIEW_SUMMARY_ITEM_TEMPLATE,
    REVIEW_DIRECTIVE_TEMPLATE,
    REVIEW_CONFIRMATION_CONTENT,
    REVIEW_CONFIRMATION_DEFAULT_INSTRUCTIONS,
    REVIEW_CONFIRMATION_DEFAULT_PROMPT,
    REVIEW_UNCLEAR_EDIT_CONTENT,
    REVIEW_UNCLEAR_GENERAL_CONTENT,
    COMPLETION_MESSAGE_TEMPLATE,
    CANCELLATION_MESSAGE_TEMPLATE,
    ACTIVE_EVENT_MESSAGE_TEMPLATE,
    REVIEW_EVENT_MESSAGE_TEMPLATE,
    COMPLETION_EVENT_MESSAGE_TEMPLATE,
    CANCELLATION_EVENT_MESSAGE_TEMPLATE,
    QUESTION_DIRECTIVE_TEMPLATE,
    INTERVIEW_PROMPT_TEMPLATE,
    INTERVIEW_CLASSIFICATION_SIGNATURE,
    REQUIRED_FIELD_DECLINE_TEMPLATE,
)

if TYPE_CHECKING:
    from jvagent.action.interview.core.interview_session import InterviewSession
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)

# Import registry access functions (decorators are in separate module)
from .decorators import (
    get_completion_handler as _get_completion_handler,
    get_input_handler as _get_input_handler,
    get_input_validator as _get_input_validator,
    get_input_directive_override as _get_input_directive_override,
    get_pending_input_handlers,
    get_pending_input_validators,
    get_pending_input_directive_overrides,
    clear_pending_registrations,
)


@dataclass
class ClassificationResult:
    """Result of unified classification and extraction routine.

    Uses unified intent types: CANCELLATION, CONFIRMATION, UPDATE, DECLINE, SUBMISSION, NONE
    """
    intent: str  # "CANCELLATION", "CONFIRMATION", "UPDATE", "DECLINE", "SUBMISSION", "NONE"
    confidence: float = 1.0  # Confidence score for the classification

    # Unified field/value structure (used for UPDATE, DECLINE, and SUBMISSION)
    field: Optional[str] = None  # Field name (for UPDATE/DECLINE intent) or null
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
        Use @input_directive_override('question_name') to customize directives after field storage.
        Use @on_interview_complete('InterviewType') to register completion handlers.

    Standard Anchors:
        Standard anchors are automatically included for all interview implementations,
        covering common scenarios like cancellation, correction, review confirmation,
        and general interview continuation. These are merged with implementation-specific
        anchors (implementation-specific first, then standard anchors appended).
    """

    description: str = "Unified orchestrator for interview system"

    # Standard anchors that are automatically included for all interview implementations
    # These cover common interview flow scenarios and ensure proper routing classification
    # Base anchor templates - will be contextualized with class name in _merge_standard_anchors
    _standard_interview_anchor_templates: List[str] = [
        # Cancellation (any state)
        "User cancels {interview_type}",
        "User stops {interview_type}",
        "User aborts {interview_type}",

        # Update (ACTIVE or REVIEW states)
        "User changes {interview_type} information",
        "User corrects {interview_type} answer",
        "User updates {interview_type} response",

        # Confirmation (REVIEW state)
        "User confirms {interview_type} information",
        "User approves {interview_type} summary",

        # Decline (ACTIVE state, non-required fields)
        "User declines to answer {interview_type} question",
        "User skips {interview_type} question",
        "User can't provide {interview_type} answer",
        "User prefers not to answer {interview_type}",

        # Submission (ACTIVE state)
        "User answers {interview_type} question",
        "User provides {interview_type} information",
        "User responds to {interview_type} prompt",
    ]

    # Class-level registries for decorator-registered handlers and validators
    # These are populated when the class is defined via decorators
    _input_handlers: Dict[str, Callable] = {}
    _input_validators: Dict[str, Callable] = {}
    _input_directive_overrides: Dict[str, Callable] = {}

    def __init_subclass__(cls, **kwargs):
        """Initialize subclass and collect decorator-registered handlers/validators."""
        super().__init_subclass__(**kwargs)

        # Initialize class-level registries
        cls._input_handlers = {}
        cls._input_validators = {}
        cls._input_directive_overrides = {}

        # Load validators/handlers/overrides from module-level registry for this class
        class_name = cls.__name__
        
        # Load from module-level registries
        # Note: We need to iterate through all registrations since we can't access the registry directly
        # The decorator module provides access functions, but for __init_subclass__ we need to
        # check all possible question names. For now, we'll rely on pending registries and
        # attribute scanning, which is the primary mechanism.
        
        # Load from pending registries (for functions decorated before class definition)
        pending_validators = get_pending_input_validators(class_name)
        for question_name, func in pending_validators.items():
            cls._input_validators[question_name] = func

        pending_handlers = get_pending_input_handlers(class_name)
        for question_name, func in pending_handlers.items():
            cls._input_handlers[question_name] = func

        pending_overrides = get_pending_input_directive_overrides(class_name)
        for question_name, func in pending_overrides.items():
            cls._input_directive_overrides[question_name] = func

        # Clear pending registrations for this class
        clear_pending_registrations(class_name)

        # Also scan class attributes for decorated functions (class methods)
        for attr_name in dir(cls):
            attr = getattr(cls, attr_name, None)
            if callable(attr) and hasattr(attr, '_interview_question_name'):
                question_name = attr._interview_question_name
                handler_type = getattr(attr, '_interview_handler_type', None)

                if handler_type == "input_handler":
                    cls._input_handlers[question_name] = attr
                elif handler_type == "input_validator" and question_name:
                    cls._input_validators[question_name] = attr
                elif handler_type == "input_directive_override" and question_name:
                    cls._input_directive_overrides[question_name] = attr

        # Note: We don't merge anchors in __init_subclass__ because we can't reliably
        # extract default values from Field/PrivateAttr descriptors at class definition time.
        # Merging is handled in on_register() and on_reload() where we have an instance
        # and can access the actual attribute value.

    @staticmethod
    def get_completion_handler(interview_type: str) -> Optional[Callable]:
        """Get completion handler for an interview type.

        Args:
            interview_type: Class name of the InterviewInteractAction

        Returns:
            Completion handler function if found, None otherwise
        """
        return _get_completion_handler(interview_type)

    @classmethod
    def get_input_handler(cls, question_name: str) -> Optional[Callable]:
        """Get input handler for a question by name (from decorator registry).

        Args:
            question_name: Name of the question

        Returns:
            Input handler function if found, None otherwise
        """
        # First check class-level registry
        handler = cls._input_handlers.get(question_name)

        # If not found, check module-level registry (in case it was registered after class definition)
        if not handler:
            handler = _get_input_handler(cls.__name__, question_name)
            if handler:
                # Cache it in class registry for future lookups
                cls._input_handlers[question_name] = handler

        return handler

    @classmethod
    def get_input_validator(cls, question_name: str) -> Optional[Callable]:
        """Get input validator for a question by name (from decorator registry).

        Checks both class-level registry and module-level registry.

        Args:
            question_name: Name of the question

        Returns:
            Input validator function if found, None otherwise
        """
        validator = cls._input_validators.get(question_name)

        # If not found, check module-level registry (in case it was registered after class definition)
        if not validator:
            validator = _get_input_validator(cls.__name__, question_name)
            if validator:
                # Move to class registry
                cls._input_validators[question_name] = validator

        return validator

    @classmethod
    def get_input_directive_override(cls, question_name: str) -> Optional[Callable]:
        """Get input directive override for a question by name (from decorator registry).

        Checks both class-level registry and module-level registry.

        Args:
            question_name: Name of the question

        Returns:
            Input directive override function if found, None otherwise
        """
        override = cls._input_directive_overrides.get(question_name)

        # If not found, check module-level registry (in case it was registered after class definition)
        if not override:
            override = _get_input_directive_override(cls.__name__, question_name)
            if override:
                # Move to class registry
                cls._input_directive_overrides[question_name] = override

        return override

    async def _call_override_function(
        self,
        func: Callable,
        *args: Any,
        **kwargs: Any
    ) -> Any:
        """Call an override function, handling both async and sync functions.

        Args:
            func: The function to call (may be async or sync)
            *args: Positional arguments to pass to the function
            **kwargs: Keyword arguments to pass to the function

        Returns:
            The result of calling the function
        """
        if inspect.iscoroutinefunction(func):
            return await func(*args, **kwargs)
        else:
            return func(*args, **kwargs)

    def _process_directive_override(
        self,
        override_result: Optional[Any],
        default_directive: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """Process directive override result and return directives to queue separately.

        Args:
            override_result: Result from directive override function (None, str, or Tuple[str, str])
            default_directive: Default directive to use if no override or for append mode

        Returns:
            Tuple of (default_directive_to_queue, custom_directive_to_queue):
            - (default_directive, None): No override, queue only default
            - (default_directive, custom_directive): Append mode or simple string - queue both separately
            - (None, custom_directive): Replace mode - queue only custom
            - (None, None): Invalid override result
        """
        if override_result is None:
            # No override, use default directive only
            return (default_directive if default_directive and default_directive.strip() else None, None)

        if isinstance(override_result, str):
            # Simple string: queue both default and custom directives separately
            default = default_directive if default_directive and default_directive.strip() else None
            return (default, override_result)

        if isinstance(override_result, tuple) and len(override_result) == 2:
            mode, directive = override_result
            if not isinstance(mode, str) or not isinstance(directive, str):
                logger.warning(
                    f"{self.get_class_name()}: Invalid directive override tuple format. "
                    f"Expected (str, str), got ({type(mode).__name__}, {type(directive).__name__})"
                )
                return (None, None)

            mode = mode.lower()
            if mode == "replace":
                # Replace mode: queue only custom directive, skip default
                return (None, directive)
            elif mode == "append":
                # Append mode: queue both default and custom directives separately
                default = default_directive if default_directive and default_directive.strip() else None
                return (default, directive)
            else:
                logger.warning(
                    f"{self.get_class_name()}: Invalid directive override mode '{mode}'. "
                    f"Expected 'append' or 'replace'"
                )
                return (None, None)

        logger.warning(
            f"{self.get_class_name()}: Invalid directive override return type. "
            f"Expected None, str, or Tuple[str, str], got {type(override_result).__name__}"
        )
        return (None, None)

    def _merge_standard_anchors(self) -> None:
        """Merge standard interview anchors with current anchors attribute.

        This method ensures standard anchors are always included, even when
        anchors are overridden in agent.yaml. Should be called from on_register()
        and on_reload() to handle runtime configuration changes.

        Standard anchors are contextualized with the class name to help distinguish
        multiple interview instances coexisting in a single agent.
        """
        # Get current anchors value (may be from agent.yaml override)
        current_anchors = getattr(self, 'anchors', [])
        if not isinstance(current_anchors, list):
            current_anchors = []

        # Generate context-specific standard anchors using class name
        interview_type = self.get_class_name()
        standard_anchors = [
            template.format(interview_type=interview_type)
            for template in self._standard_interview_anchor_templates
        ]

        # Merge: current anchors first, then standard anchors appended
        # Remove duplicates while preserving order
        merged_anchors = list(dict.fromkeys(current_anchors + standard_anchors))

        # Update the anchors attribute
        self.anchors = merged_anchors

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
        default="gpt-4o",
        description="Default model name; use a capable model for best results"
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

    # DSPy Integration
    use_dspy: bool = attribute(
        default=False,
        description="Use DSPy module for classification (enables optimization via DSPy teleprompters)"
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

    # Consolidated review directive template (for REVIEW state)
    # Single template handling all scenarios: confirmation, unclear edit, unclear general
    review_directive_template: str = attribute(
        default=REVIEW_DIRECTIVE_TEMPLATE,
        description="Consolidated review directive template. Use with REVIEW_CONFIRMATION_CONTENT, REVIEW_UNCLEAR_EDIT_CONTENT, or REVIEW_UNCLEAR_GENERAL_CONTENT. Defaults to REVIEW_DIRECTIVE_TEMPLATE from prompts.py",
    )

    # Confirmation content template
    confirmation_content_template: str = attribute(
        default=REVIEW_CONFIRMATION_CONTENT,
        description="Confirmation content template with {summary}, {instructions}, {prompt} placeholders. Defaults to REVIEW_CONFIRMATION_CONTENT from prompts.py",
    )

    # Default values for confirmation content
    confirmation_instructions: str = attribute(
        default=REVIEW_CONFIRMATION_DEFAULT_INSTRUCTIONS,
        description="Default instructions text for review confirmation. Used in {instructions} placeholder. Defaults to REVIEW_CONFIRMATION_DEFAULT_INSTRUCTIONS from prompts.py",
    )

    confirmation_prompt: str = attribute(
        default=REVIEW_CONFIRMATION_DEFAULT_PROMPT,
        description="Default prompt text for review confirmation. Used in {prompt} placeholder. Defaults to REVIEW_CONFIRMATION_DEFAULT_PROMPT from prompts.py",
    )

    # Unclear response content templates
    unclear_edit_content_template: str = attribute(
        default=REVIEW_UNCLEAR_EDIT_CONTENT,
        description="Unclear edit content template with {field_list} placeholder. Defaults to REVIEW_UNCLEAR_EDIT_CONTENT from prompts.py",
    )

    unclear_general_content_template: str = attribute(
        default=REVIEW_UNCLEAR_GENERAL_CONTENT,
        description="Unclear general content template. Defaults to REVIEW_UNCLEAR_GENERAL_CONTENT from prompts.py",
    )

    # Interview prompt template
    interview_prompt: str = attribute(
        default=INTERVIEW_PROMPT_TEMPLATE,
        description="Interview prompt template that combines intent detection (CANCELLATION, CONFIRMATION, UPDATE, SUBMISSION) with response extraction in a single LLM call. Defaults to INTERVIEW_PROMPT_TEMPLATE from prompts.py",
    )

    # DSPy signature docstring (single source of truth, can be overridden in agent.yaml for runtime customization)
    interview_classification_signature: str = attribute(
        default=INTERVIEW_CLASSIFICATION_SIGNATURE,
        description="DSPy signature docstring for InterviewClassification. Can be overridden in agent.yaml for runtime customization. Defaults to INTERVIEW_CLASSIFICATION_SIGNATURE from prompts.py",
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

    # Required field decline template (for when user tries to decline a required field)
    required_field_decline_template: str = attribute(
        default=REQUIRED_FIELD_DECLINE_TEMPLATE,
        description="Template for insisting user answer a required field when they try to decline. Uses {field_display} and {question} placeholders. Defaults to REQUIRED_FIELD_DECLINE_TEMPLATE from prompts.py",
    )

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
        # Explicitly add completion event BEFORE cleaning up the session
        # This ensures the event is recorded even if the session is removed
        # Mark event as added to prevent _queue_directive from adding it again
        completion_event = self.completion_event_message_template.format(class_name=self.get_class_name())
        await visitor.add_event(completion_event)
        self._event_added = True  # Prevent duplicate event addition in _queue_directive

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
                await self._queue_directive(
                    visitor,
                    self.completion_message_template
                )
        else:
            # No completion handler registered, send generic message
            await self._queue_directive(
                visitor,
                self.completion_message_template
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
        await self._queue_directive(
            visitor,
            self.cancellation_message_template
        )

        # Clean up and remove the session
        try:
            await session.cleanup()
            # Clear session reference from visitor
            visitor.interview_session = None
        except Exception as e:
            logger.error(f"{self.get_class_name()}: Failed to cleanup cancelled session: {e}", exc_info=True)

    def _sort_by_question_order(
        self,
        fields: List[str],
        session: InterviewSession
    ) -> List[str]:
        """Sort fields by their position in question_index.

        This ensures fields are processed in the logical order defined by the
        interview schema, which is important for conditional edge evaluation.

        Args:
            fields: List of field names to sort
            session: Interview session with question_index

        Returns:
            Sorted list of field names in question_index order
        """
        # Create a map of field name to index position
        field_to_index = {}
        for idx, question_config in enumerate(session.question_index):
            field_name = question_config.get("name", "")
            if field_name:
                field_to_index[field_name] = idx

        # Sort fields by their index, unknown fields go to the end
        def get_sort_key(field: str) -> int:
            return field_to_index.get(field, len(session.question_index))

        return sorted(fields, key=get_sort_key)

    async def _update_reachable_questions(
        self,
        session: InterviewSession,
        question_walker: QuestionWalker
    ) -> None:
        """Re-evaluate which questions are reachable after new answers.

        This method is called after storing a valid response to update
        the session's understanding of which questions should be processed.
        Conditional edges may cause some questions to be skipped.

        Currently, this is a no-op as the reachability check happens
        dynamically in should_process_question. This method exists for
        potential future optimizations (e.g., caching reachable questions).

        Args:
            session: Interview session
            question_walker: QuestionWalker instance
        """
        # Currently a no-op - reachability is checked dynamically
        # This method exists for potential future optimizations
        pass

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
        # Check cache first (stored in session context)
        if session.context is None:
            session.context = {}
        
        node_cache = session.context.get("_question_node_cache", {})
        if field in node_cache:
            cached_node_id = node_cache[field]
            try:
                from jvspatial.core import Node
                cached_node = await Node.get(cached_node_id)
                if cached_node and isinstance(cached_node, QuestionNode):
                    return cached_node
                else:
                    # Cache entry is stale, remove it
                    del node_cache[field]
            except Exception:
                # Cache entry is invalid, remove it
                del node_cache[field]
        
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
        lines = []
        if self.summary_header_template and self.summary_header_template.strip():
            lines.append(self.summary_header_template)

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

        # Build confirmation section using confirmation content template
        confirmation_section = self.confirmation_content_template.format(
            summary=summary,
            instructions=self.confirmation_instructions,
            prompt=self.confirmation_prompt,
        )

        # Use consolidated directive template with confirmation section populated
        return self.review_directive_template.format(
            confirmation_section=confirmation_section,
            unclear_edit_section="",
            unclear_general_section="",
        )

    async def _queue_directive(
        self,
        visitor: "InteractWalker",
        directive: str
    ) -> None:
        """Queue a directive for later response generation.

        The event is determined automatically based on the session state and added only once
        per execution, even if multiple directives are queued.

        Args:
            visitor: InteractWalker
            directive: Directive string to queue
        """
        if directive and directive.strip():
            # Add event only once per execution, determined by session state
            if not self._event_added:
                # Determine event based on session state from visitor
                session = getattr(visitor, 'interview_session', None)
                if session:
                    if session.state == InterviewState.COMPLETED:
                        # Completion event is already added explicitly in _generate_completed_directive
                        # Skip to avoid duplicate events
                        event_name = None
                    elif session.state == InterviewState.CANCELLED:
                        event_name = self.cancellation_event_message_template.format(class_name=self.get_class_name())
                    elif session.state == InterviewState.REVIEW:
                        event_name = self.review_event_message_template.format(class_name=self.get_class_name())
                    else:
                        # Default to active event for ACTIVE state or if state not recognized
                        event_name = self.active_event_message_template.format(class_name=self.get_class_name())
                else:
                    # No session available, default to active event
                    event_name = self.active_event_message_template.format(class_name=self.get_class_name())

                # Only add event if one was determined (skip if COMPLETED state already handled)
                if event_name:
                    await visitor.add_event(event_name)
                    self._event_added = True
                else:
                    # Event already added explicitly, just mark as added
                    self._event_added = True

            await visitor.add_directive(directive)
        else:
            logger.warning(f"{self.get_class_name()}: Attempted to queue empty directive")

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

        Uses router interpretation as primary source when available, providing structured
        context for classification and extraction.

        Args:
            session: Interview session
            utterance: User's utterance (fallback if interpretation not available)
            interaction: Current interaction
            visitor: InteractWalker

        Returns:
            ClassificationResult with unified intent and extracted data
        """
        # Skip classification for terminal states
        if session.state == InterviewState.COMPLETED or session.state == InterviewState.CANCELLED:
            return ClassificationResult(intent="NONE")

        # Build user input - prioritize interpretation when available
        interpretation_available = interaction.interpretation and interaction.interpretation.strip()
        if interpretation_available:
            # Use interpretation as primary source, include utterance only for context if different
            user_input = interaction.interpretation
            if utterance and utterance.strip() and utterance.strip() != interaction.interpretation.strip():
                # Only include utterance if it adds context (is different from interpretation)
                user_input = f"Interpretation: {interaction.interpretation}\nUser's utterance: {utterance}"
        elif utterance and utterance.strip():
            user_input = utterance
        else:
            return ClassificationResult(intent="NONE")

        # Use DSPy if enabled, otherwise use legacy implementation
        if self.use_dspy:
            return await self._classify_with_dspy(session, user_input, interaction, visitor)

        # Unified classification and extraction using single prompt
        try:
            # Build context for unified prompt
            context = self._build_classification_context(session)

            prompt = self.interview_prompt.format(
                user_input=user_input,
                current_state=context["current_state"],
                answered_fields=context["answered_fields"],
                entities_to_extract=context["entities_to_extract"],
                required_fields_info=context["required_fields_info"]
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
                    with_response=True,
                    with_interpretation=False,
                    with_event=False,
                    max_statement_length=self.max_statement_length,
                )

            # Call LLM with unified prompt
            # Use interpretation as primary text when available (already in user_input)
            primary_text = interaction.interpretation if interpretation_available else utterance
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
            # Normalize field - handle string "null" from JSON
            field_value = result.get("field")
            if field_value and isinstance(field_value, str):
                field_str = field_value.strip().lower()
                if field_str == "null" or field_str == "none":
                    field_value = None

            classification_result = ClassificationResult(
                intent=intent,
                confidence=confidence,
                field=field_value,
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

    def _build_classification_context(
        self,
        session: InterviewSession
    ) -> Dict[str, str]:
        """Build minimal context for classification.

        Args:
            session: Interview session

        Returns:
            Dictionary with current_state, answered_fields, entities_to_extract, required_fields_info
        """
        current_state = session.state.value

        # Format answered fields (minimal - just field names)
        answered_fields = session.get_answered_questions()
        answered_fields_str = ", ".join(answered_fields) if answered_fields else "None"

        # Get unanswered questions for extraction
        unanswered = session.get_unanswered_questions()
        if session.active_question_key and session.active_question_key in unanswered:
            active_questions = [q for q in session.question_index if q.get("name") == session.active_question_key]
        else:
            active_questions = [q for q in session.question_index if q.get("name") in unanswered]

        # Build entities list for extraction with required field information
        entities_list = []
        required_fields = set(session.get_required_questions())

        for item in active_questions:
            key = item.get('name')
            constraints = item.get('constraints', {})
            if not key or not constraints:
                continue
            desc = constraints.get('description', '')
            other_constraints = {k: v for k, v in constraints.items() if k != 'description'}
            constraint_strs = [f"{k}: {v}" for k, v in other_constraints.items()]
            constraint_part = f" ({', '.join(constraint_strs)})" if constraint_strs else ""
            is_required = key in required_fields
            required_marker = " [REQUIRED]" if is_required else " [OPTIONAL]"
            entities_list.append(f"- {key}: {desc}{constraint_part}{required_marker}")

        entities_to_extract = "\n".join(entities_list) if entities_list else "None (all questions answered)"

        # Build required fields info (simplified - comma-separated)
        required_fields_info = ", ".join(sorted(required_fields)) if required_fields else "None"

        return {
            "current_state": current_state,
            "answered_fields": answered_fields_str,
            "entities_to_extract": entities_to_extract,
            "required_fields_info": required_fields_info,
        }

    async def _classify_with_dspy(
        self,
        session: InterviewSession,
        user_input: str,
        interaction: Interaction,
        visitor: "InteractWalker"
    ) -> ClassificationResult:
        """DSPy-based classification and extraction routine.

        Uses DSPy modules with typed signatures for classification, enabling
        optimization via DSPy teleprompters (BootstrapFewShot, MIPROv2, etc.)
        and evaluation with dspy.Evaluate.

        Args:
            session: Interview session
            user_input: User's input (typically with reasoning)
            interaction: Current interaction
            visitor: InteractWalker

        Returns:
            ClassificationResult with unified intent and extracted data
        """
        try:
            # Import DSPy components
            import dspy
            from jvagent.action.model.dspy import DSPyLM
            from jvagent.action.interview.dspy import InterviewClassifier

            # Build context for classification
            context = self._build_classification_context(session)

            # Get conversation history if needed
            conversation_history = None
            formatted_history = None
            if self.use_history:
                conversation_history = await self._get_conversation_history(
                    interaction,
                    self.history_limit,
                    with_utterance=True,
                    with_response=True,
                    with_interpretation=False,
                    with_event=False,
                    max_statement_length=self.max_statement_length,
                )
                # Format history for DSPy signature
                from jvagent.action.model.dspy import format_conversation_history_for_dspy
                formatted_history = format_conversation_history_for_dspy(conversation_history)

            # Get model action
            model_action = await self.get_model_action(required=True)
            if not model_action:
                logger.warning(f"{self.get_class_name()}: Could not get model action for DSPy classification")
                return ClassificationResult(intent="NONE")

            # Create DSPy LM adapter
            # Pass model, temperature, and max_tokens to allow agent.yaml overrides
            lm = DSPyLM(
                model_action=model_action,
                model_type="chat",
                model=self.model,
                temperature=self.model_temperature,
                max_tokens=self.model_max_tokens,
            )

            # Configure DSPy with the adapter
            with dspy.context(lm=lm):
                # Create classifier instance with action instance for signature docstring
                classifier = InterviewClassifier(action_instance=self)

                # Build kwargs for classifier, include history if available
                classifier_kwargs = {
                    "user_input": user_input,
                    "current_state": context["current_state"],
                    "answered_fields": context["answered_fields"],
                    "entities_to_extract": context["entities_to_extract"],
                    "required_fields_info": context["required_fields_info"],
                }
                if formatted_history:
                    classifier_kwargs["conversation_history"] = formatted_history

                # Call classifier with async forward
                classification_result = await classifier.aforward(**classifier_kwargs)

                return classification_result

        except Exception as e:
            logger.error(
                f"{self.get_class_name()}: Failed to classify/extract via DSPy: {e}",
                exc_info=True
            )
            return ClassificationResult(intent="NONE")

    async def on_register(self) -> None:
        """Register the action and build question nodes.

        Note: Errors are automatically logged by the base Action class.
        """

        # Merge standard anchors with any anchors set via agent.yaml
        self._merge_standard_anchors()

        # Validate question_index is defined
        if not self.question_index:
            logger.warning(f"{self.get_class_name()}: question_index is empty. Define questions in subclass or agent.yaml")

        # Build QuestionNode chain
        service = InterviewService(self)
        await service.build_question_nodes()

    async def on_reload(self) -> None:
        """Reload the action - rebuild question nodes if question_index changed."""

        # Merge standard anchors with any anchors set via agent.yaml (may have changed on reload)
        self._merge_standard_anchors()

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
        # Initialize event tracking (event added only once per execution)
        self._event_added = False

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
        service = InterviewService(self)
        classification_result = await service.classify_and_extract(
            session,
            utterance,
            interaction,
            visitor
        )

        # Generate directive based on state and classification
        await service.generate_directive(
            session,
            classification_result,
            visitor,
            interaction
        )

        # Reset event tracking for next execution
        self._event_added = False

    async def _get_conversation_history(
        self,
        interaction: Interaction,
        history_limit: int,
        with_utterance: bool = True,
        with_response: bool = True,
        with_interpretation: bool = False,
        with_event: bool = False,
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
            # excluded=interaction.id,
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