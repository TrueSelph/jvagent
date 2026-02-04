"""Interview Action Implementation

Unified interview system for gathering structured information from users through
multi-turn conversations with validation, revision, and confirmation flows.

This is an abstract base class that should be extended to create concrete
interview implementations. Each subclass should define its own question_graph
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
import sys
from abc import ABC
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple, Union

from jvagent.action.interact.base import InteractAction
from jvagent.memory import Interaction
from jvspatial.core.annotations import attribute

from .core.foundation.enums import Intent, InterviewState, ValidationStatus
from .core.session.interview_service import InterviewService
from .core.session.interview_session import InterviewSession
from .core.graph.question_node import QuestionNode
from .core.graph.question_walker import QuestionWalker
from .core.utils.session_utils import cleanup_session, sort_fields_by_question_order
from .core.utils.cache_utils import QuestionNodeCache
from .core.utils.constants import CACHE_KEY_QUESTION_NODES
from .core.classification.classification_handler import ClassificationHandler, ClassificationResult
from .core.processing.directive_builder import DirectiveBuilder
from .core.foundation.exceptions import QuestionNotFoundError
from .core.foundation.config import InterviewConfig, ModelConfig, TemplateConfig

if TYPE_CHECKING:
    from jvagent.action.interview.core.session.interview_session import InterviewSession
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)

# Import registry access functions (decorators are in separate module)
from .core.foundation.decorators import (
    get_completion_handler as _get_completion_handler,
    get_input_handler as _get_input_handler,
    get_input_validator as _get_input_validator,
    get_input_directive_override as _get_input_directive_override,
    get_input_context_provider as _get_input_context_provider,
    get_pending_input_handlers,
    get_pending_input_validators,
    get_pending_input_directive_overrides,
    get_pending_branch_functions,
    get_pending_input_context_providers,
    clear_pending_registrations,
    flush_module_registrations_for_class,
)


# ClassificationResult moved to classification_handler module


class InterviewInteractAction(InteractAction, ABC):
    """Unified interview system orchestrator.

    This action manages the complete interview lifecycle:
    1. Creates and chains QuestionNode and StateNode instances from question_graph
    2. Manages InterviewSession state (ACTIVE, REVIEW, COMPLETED, CANCELLED)
    3. Uses unified classification to detect intent and extract field values
    4. Generates appropriate directives based on state and classification results
    5. Handles state transitions within the same interaction when appropriate

    The system uses a single unified prompt that accepts both utterance and
    interpretation (when available) to detect intent and extract information
    in one LLM call.

    Attributes:
        question_graph: List of question configurations defining the interview graph schema

    Decorator Support:
        Use @input_handler('question_name') and @input_validator('question_name') decorators
        to register handlers and validators instead of embedding them in question_graph.
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
    # Base anchor templates - will be contextualized with class name in _merge_standard_anchors
    # Covers: cancellation, update, confirmation, decline, submission
    _standard_interview_anchor_templates: List[str] = [
        "User cancels {interview_type}",
        "User corrects or updates {interview_type}",
        "User confirms {interview_type}",
        "User skips {interview_type} question",
        "User declines to answer a question from {interview_type}",
        "User answers {interview_type} question",
        "User provides {interview_type} information",
    ]

    # Class-level registries for decorator-registered handlers and validators
    # These are populated when the class is defined via decorators
    _input_handlers: Dict[str, Callable] = {}
    _input_validators: Dict[str, Callable] = {}
    _input_directive_overrides: Dict[str, Callable] = {}
    
    # Instance-level handlers
    _classification_handler: Optional[ClassificationHandler] = None
    _directive_builder: Optional[DirectiveBuilder] = None
    
    @property
    def classification_handler(self) -> ClassificationHandler:
        """Get or create classification handler."""
        if self._classification_handler is None:
            self._classification_handler = ClassificationHandler(self)
        return self._classification_handler
    
    @property
    def directive_builder(self) -> DirectiveBuilder:
        """Get or create directive builder."""
        if self._directive_builder is None:
            self._directive_builder = DirectiveBuilder(self)
        return self._directive_builder

    weight: int = attribute(
        default=-40,
        description="Execution weight (runs after InteractRouter but before PersonaAction)",
    )

    question_graph: List[Dict[str, Any]] = attribute(
        default_factory=list,
        description="List of question configurations defining the interview graph schema. Can be overridden in agent.yaml. Supports conditional branching via 'branches' and 'default_next'.",
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


    @property
    def config(self) -> InterviewConfig:
        """Interview config from metadata (always InterviewConfig, not raw dict)."""
        raw = super().config
        return InterviewConfig.from_dict(raw if isinstance(raw, dict) else {})

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

        # Register module-level input_context_provider and branch_function (defined before class)
        module = sys.modules.get(cls.__module__)
        flush_module_registrations_for_class(class_name, module)

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


    async def _generate_completed_directive(
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
        await self.directive_builder.generate_completed_directive(session, visitor)

    async def _generate_cancelled_directive(
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
        await self.directive_builder.generate_cancelled_directive(session, visitor)


    async def _update_reachable_questions(
        self,
        session: InterviewSession,
        question_walker: QuestionWalker,
        just_answered_field: Optional[str] = None
    ) -> bool:
        """Re-evaluate branches after storing a response.

        If the just-answered field has conditional branches, evaluate them.
        If a branch targets a state node, execute the state transition immediately.

        Args:
            session: Interview session
            question_walker: QuestionWalker instance
            just_answered_field: Optional field name that was just answered

        Returns:
            True if state transition occurred, False otherwise
        """
        if not just_answered_field:
            return False

        # Get question config for the field just answered
        question_config = session.get_question_by_name(just_answered_field)
        if not question_config:
            return False

        # Check for branches
        branches = question_config.get("branches", [])
        if not branches:
            return False

        # Evaluate branches
        from .core.graph.question_branch_evaluator import QuestionBranchEvaluator

        for branch in branches:
            condition = branch.get("condition", {})
            target = branch.get("target")
            response_value = session.responses.get(just_answered_field)
            
            logger.debug(
                f"Evaluating branch: question={just_answered_field}, "
                f"condition={condition}, target={target}, "
                f"response_value={response_value!r}"
            )
            
            # Question is implicit - condition always evaluates against just_answered_field
            # Note: visitor not directly available here, but branch functions can work without it
            if await QuestionBranchEvaluator.matches(condition, session, implicit_question=just_answered_field, visitor=None):
                logger.info(
                    f"Branch condition MATCHED: {just_answered_field} {condition} -> {target}"
                )
                if target:
                    # Check if it's a state target
                    if question_walker._is_state_target(target):
                        logger.info(
                            f"Target '{target}' is a state target, initiating state transition"
                        )
                        # Execute state transition NOW
                        handled = await question_walker._handle_state_target(
                            target, session, interview_action=self
                        )
                        if handled:
                            # Ensure session is saved after state transition
                            await session.save()
                            logger.info(
                                f"State transition to '{target}' completed successfully"
                            )
                            return True  # State transition occurred
                        else:
                            logger.warning(
                                f"State transition to '{target}' was not handled"
                            )
                    else:
                        logger.debug(
                            f"Target '{target}' is a question target, normal flow will handle it"
                        )
                    # If it's a question target, do nothing (normal flow handles it)
                    break
            else:
                logger.debug(
                    f"Branch condition NOT matched: {just_answered_field} {condition} "
                    f"(response_value={response_value!r})"
                )

        return False

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
        # Use cache utility
        cache = QuestionNodeCache(session)
        cached_node = await cache.get_cached_node_by_id(field)
        if cached_node:
            return cached_node
        
        # Find question config
        question_config = session.get_question_by_name(field)
        if not question_config:
            raise QuestionNotFoundError(field)

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
        
        # Cache the node
        if question_node:
            cache.set(field, question_node.id)

        return question_node

    def _format_summary(self, session: InterviewSession) -> str:
        """Format collected responses as a summary.

        Delegates to DirectiveBuilder.

        Args:
            session: Interview session

        Returns:
            Formatted summary string
        """
        return self.directive_builder.format_summary(session)

    def _build_confirmation_directive(self, session: InterviewSession) -> str:
        """Build the complete confirmation directive from consolidated template.

        Delegates to DirectiveBuilder.

        Args:
            session: Interview session

        Returns:
            Complete confirmation directive string
        """
        return self.directive_builder.build_confirmation_directive(session)

    async def _queue_directive(
        self,
        visitor: "InteractWalker",
        directive: str
    ) -> None:
        """Queue a directive for later response generation.

        Delegates to DirectiveBuilder.

        Args:
            visitor: InteractWalker
            directive: Directive string to queue
        """
        await self.directive_builder.queue_directive(visitor, directive)

    async def _classify_and_extract(
        self,
        session: InterviewSession,
        utterance: str,
        interaction: Interaction,
        visitor: "InteractWalker"
    ) -> ClassificationResult:
        """Unified classification and extraction routine.

        Delegates to ClassificationHandler for actual implementation.

        Args:
            session: Interview session
            utterance: User's utterance (fallback if interpretation not available)
            interaction: Current interaction
            visitor: InteractWalker

        Returns:
            ClassificationResult with unified intent and extracted data
        """
        return await self.classification_handler.classify_and_extract(
            session, utterance, interaction, visitor
        )

    def _get_question_graph(self) -> List[Dict[str, Any]]:
        """Get question graph.

        Returns:
            List of question configuration dictionaries
        """
        return self.question_graph

    async def _get_or_create_session(self, conversation: Any) -> InterviewSession:
        """Load existing active session or create and attach a new one.

        Ensures a loaded session has question_graph populated (storage may not
        persist or restore it). Caller must inject the returned session into the
        visitor.

        Args:
            conversation: Conversation node to query/attach session to.

        Returns:
            InterviewSession for this interview type (ACTIVE or not terminal).
        """
        interview_type = self.get_class_name()
        session = await conversation.node(
            node=[{"InterviewSession": {
                "state": {"$nin": [InterviewState.COMPLETED.value, InterviewState.CANCELLED.value]}
            }}],
            interview_type=interview_type,
        )
        if not session:
            question_graph = self._get_question_graph()
            session = await InterviewSession.create(
                agent_id=self.agent_id,
                conversation_id=conversation.id,
                interview_type=interview_type,
                question_graph=question_graph,
                state=InterviewState.ACTIVE,
            )
            session.started_at = datetime.now()
            await session.save()
            await conversation.connect(session)
        else:
            if not (session.question_graph and len(session.question_graph) > 0):
                session.question_graph = self._get_question_graph()
                await session.save()
        return session

    async def on_register(self) -> None:
        """Register the action and build question nodes.

        Note: Errors are automatically logged by the base Action class.
        """

        # Merge standard anchors with any anchors set via agent.yaml
        self._merge_standard_anchors()

        # Get question graph
        question_graph = self._get_question_graph()
        
        # Validate question graph is defined
        if not question_graph:
            logger.warning(f"{self.get_class_name()}: question_graph is empty. Define questions in subclass or agent.yaml")

        # Validate graph structure
        from .core.graph.graph_validator import QuestionGraphValidator
        validator = QuestionGraphValidator(question_graph, interview_type=self.__class__.__name__)
        validation_report = await validator.validate()
        
        if not validation_report.is_valid():
            validation_report.log_issues(self.get_class_name())
            raise ValueError(
                f"{self.get_class_name()}: Question graph validation failed. "
                f"See logs for details."
            )
        
        if validation_report.has_warnings():
            validation_report.log_issues(self.get_class_name())

        # Build QuestionNode and StateNode graph
        service = InterviewService(self)
        await service.build_question_graph()

    async def on_reload(self) -> None:
        """Reload the action - rebuild question nodes if question_graph changed."""

        # Merge standard anchors with any anchors set via agent.yaml (may have changed on reload)
        self._merge_standard_anchors()

        # Get current question node labels to detect changes
        existing_nodes = await self.nodes(direction="out", node=QuestionNode)
        existing_labels = {n.label for n in existing_nodes}

        # Get expected labels from question_graph
        question_graph = self._get_question_graph()
        expected_labels = {q.get("name", "") for q in question_graph if q.get("name")}

        # If labels changed, rebuild question nodes
        if existing_labels != expected_labels:
            # Disconnect and delete old question nodes
            for node in existing_nodes:
                await self.disconnect(node)
                await node.delete()
            # Also delete state nodes
            from .core.graph.state_node import StateNode
            existing_state_nodes = await self.nodes(direction="out", node=StateNode)
            for node in existing_state_nodes:
                await self.disconnect(node)
                await node.delete()
            # Rebuild using QuestionGraphBuilder
            service = InterviewService(self)
            await service.build_question_graph()


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
        self.directive_builder.reset_event_tracking()

        interaction = visitor.interaction
        if not interaction:
            logger.warning(f"{self.get_class_name()}: No interaction available")
            return

        # Get conversation from interaction
        conversation = await interaction.get_conversation()
        if not conversation:
            logger.warning(f"{self.get_class_name()}: No conversation available")
            return

        # Get or create session for this conversation
        session = await self._get_or_create_session(conversation)
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
        self.directive_builder.reset_event_tracking()

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
        from .core.utils import extract_json
        return extract_json(response, context=self.get_class_name())

    async def get_model_action(self, required: bool = False):
        """Get the language model action.

        Args:
            required: If True, raises error if action not found

        Returns:
            LanguageModelAction instance or None
        """
        try:
            model_action_type = self.config.model.model_action_type
            if model_action_type:
                model_action = await self.get_action(model_action_type)
            else:
                # Fallback to first available LanguageModelAction
                from jvagent.action.model.language.base import LanguageModelAction
                model_action = await self.get_action(LanguageModelAction)

            if not model_action and required:
                raise ValueError(f"{self.get_class_name()}: Model action not found (model_action_type={model_action_type})")

            return model_action
        except Exception as e:
            if required:
                raise
            logger.warning(f"{self.get_class_name()}: Could not get model action: {e}")
            return None