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

import logging
import sys
from abc import ABC
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Union

from jvspatial.core import Node
from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.memory import Interaction

from .core.classification.classification_handler import (
    ClassificationHandler,
    ClassificationResult,
)
from .core.foundation.enums import Intent, InterviewState
from .core.foundation.exceptions import QuestionNotFoundError
from .core.foundation.prompts import (
    ACTIVE_TASK_DESCRIPTION_TEMPLATE,
    CANCELLATION_MESSAGE,
    COMPLETION_MESSAGE,
    INTERVIEW_PROMPT,
    QUESTION_DIRECTIVE,
    REQUIRED_FIELD_DECLINE,
    REVIEW_CONFIRMATION_DEFAULT_INSTRUCTIONS,
    REVIEW_CONFIRMATION_DEFAULT_PROMPT,
    REVIEW_CONFIRMATION_DIRECTIVE,
    REVIEW_SUMMARY_HEADER,
    REVIEW_SUMMARY_ITEM,
    REVIEW_UNCLEAR_EDIT_DIRECTIVE,
    REVIEW_UNCLEAR_GENERAL_DIRECTIVE,
    UPDATE_PROMPT_FOR_VALUE,
)
from .core.graph.interview_walker import InterviewWalker
from .core.graph.question_edge import QuestionEdge
from .core.graph.question_graph_builder import QuestionGraphBuilder
from .core.graph.question_node import QuestionNode
from .core.graph.question_path_walker import QuestionPathWalker
from .core.graph.state_node import StateNode
from .core.processing.directive_builder import DirectiveBuilder
from .core.processing.target_resolver import TargetResolver
from .core.session.interview_session import InterviewSession
from .core.utils.cache_utils import BranchCache, QuestionNodeCache
from .core.utils.session_utils import get_graph_order

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.action.interview.core.session.interview_session import InterviewSession

logger = logging.getLogger(__name__)

TASK_TYPE_INTERVIEW = "INTERVIEW"

# Import registry access functions (decorators are in separate module)
from .core.foundation.decorators import (
    RegistryManager,
    clear_pending_registrations,
    flush_module_registrations_for_class,
)
from .core.foundation.decorators import get_cancelled_handler as _get_cancelled_handler
from .core.foundation.decorators import (
    get_completion_handler as _get_completion_handler,
)
from .core.foundation.decorators import (
    get_input_context_provider as _get_input_context_provider,
)
from .core.foundation.decorators import (
    get_input_directive_override as _get_input_directive_override,
)
from .core.foundation.decorators import get_input_handler as _get_input_handler
from .core.foundation.decorators import (
    get_input_review_override as _get_input_review_override,
)
from .core.foundation.decorators import get_input_validator as _get_input_validator
from .core.foundation.decorators import get_review_handler as _get_review_handler


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

    # Task type for router routing (ensures at most one interview runs at a time)
    task_type: str = TASK_TYPE_INTERVIEW

    # Standard anchors that are automatically included for all interview implementations
    # Base anchor templates - will be contextualized with class name in _merge_standard_anchors
    # Covers: cancellation, update, confirmation, decline, submission
    _standard_interview_anchor_templates: List[str] = [
        "IF {interview_type} entry is listed under ACTIVE TASKS AND the user requests to cancel or abandon the task.",
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
    _input_review_override: Optional[Callable] = None

    # Instance-level handlers
    _classifier: Optional[ClassificationHandler] = None
    _question_builder: Optional[QuestionGraphBuilder] = None
    _directive_builder: Optional[DirectiveBuilder] = None

    @property
    def classifier(self) -> ClassificationHandler:
        """Get or create classification handler."""
        if self._classifier is None:
            self._classifier = ClassificationHandler(self)
        return self._classifier

    @property
    def question_builder(self) -> QuestionGraphBuilder:
        """Get or create question graph builder."""
        if self._question_builder is None:
            self._question_builder = QuestionGraphBuilder(self)
        return self._question_builder

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

    # =========================================================================
    # Model Configuration Attributes
    # =========================================================================
    model_action_type: str = attribute(
        default="OpenAILanguageModelAction",
        description="Type of language model action to use",
    )
    model: str = attribute(
        default="gpt-4o", description="Name of the language model to use"
    )
    model_temperature: float = attribute(
        default=0.1, description="Sampling temperature for the model"
    )
    model_max_tokens: int = attribute(
        default=8192, description="Maximum tokens for model response"
    )
    use_history: bool = attribute(
        default=True, description="Whether to include conversation history"
    )
    max_statement_length: int = attribute(
        default=500, description="Maximum length of history statements"
    )
    history_limit: int = attribute(
        default=3, description="Maximum number of history turns to include"
    )

    # =========================================================================
    # Template Configuration Attributes
    # =========================================================================
    summary_header: str = attribute(
        default=REVIEW_SUMMARY_HEADER, description="Header for interview summary"
    )
    summary_item: str = attribute(
        default=REVIEW_SUMMARY_ITEM, description="Template for summary items"
    )
    review_confirmation: str = attribute(
        default=REVIEW_CONFIRMATION_DIRECTIVE,
        description="Template for review confirmation prompt",
    )
    confirmation_instructions: str = attribute(
        default=REVIEW_CONFIRMATION_DEFAULT_INSTRUCTIONS,
        description="Default instructions for review confirmation",
    )
    confirmation_prompt: str = attribute(
        default=REVIEW_CONFIRMATION_DEFAULT_PROMPT,
        description="Default prompt for review confirmation",
    )
    review_unclear_edit: str = attribute(
        default=REVIEW_UNCLEAR_EDIT_DIRECTIVE,
        description="Directive for unclear edit requests",
    )
    review_unclear_general: str = attribute(
        default=REVIEW_UNCLEAR_GENERAL_DIRECTIVE,
        description="Directive for general unclear review feedback",
    )
    update_prompt_for_value: str = attribute(
        default=UPDATE_PROMPT_FOR_VALUE, description="Template for update prompt"
    )
    completion_message: str = attribute(
        default=COMPLETION_MESSAGE, description="Message shown on completion"
    )
    cancellation_message: str = attribute(
        default=CANCELLATION_MESSAGE, description="Message shown on cancellation"
    )
    question_directive: str = attribute(
        default=QUESTION_DIRECTIVE, description="Directive for asking questions"
    )
    required_field_decline: str = attribute(
        default=REQUIRED_FIELD_DECLINE,
        description="Template for required field decline",
    )
    interview_prompt: str = attribute(
        default=INTERVIEW_PROMPT, description="Base prompt for interview orchestration"
    )
    active_task_description: str = attribute(
        default=ACTIVE_TASK_DESCRIPTION_TEMPLATE,
        description="Guidance recorded on the active control-task that steers how "
        "the agent handles off-topic input mid-interview. Placeholders: "
        "{action_title}, {action_description}. Default answers divergences "
        "without redirecting; override (e.g. in the agent YAML) to keep the user "
        "on the pending step.",
    )

    # =========================================================================
    # Classification Configuration Attributes
    # =========================================================================
    context_list_compact_threshold: int = attribute(
        default=5, description="Max list length for inline display"
    )
    context_options_text: str = attribute(
        default="options available", description="Text for long option lists"
    )
    decline_value: str = attribute(
        default="n/a", description="Value stored when an optional field is declined"
    )
    require_structured_reasoning: bool = attribute(
        default=True, description="Require structured reasoning from LLM"
    )
    include_few_shot_examples: bool = attribute(
        default=True, description="Include examples in prompt"
    )
    max_examples: int = attribute(
        default=5, description="Max number of examples to include"
    )
    enable_reference_resolution: bool = attribute(
        default=True, description="Enable reference resolution section"
    )
    enable_composition: bool = attribute(
        default=True, description="Enable multi-turn composition section"
    )

    # =========================================================================
    # Interview Execution Attributes
    # =========================================================================
    auto_confirm: bool = attribute(
        default=False, description="Skip confirmation prompt in REVIEW state"
    )

    def __init_subclass__(cls, **kwargs):
        """Initialize subclass and collect decorator-registered handlers/validators."""
        super().__init_subclass__(**kwargs)

        # Initialize class-level registries
        cls._input_handlers = {}
        cls._input_validators = {}
        cls._input_directive_overrides = {}
        cls._input_review_override = None

        # Load validators/handlers/overrides from module-level registry for this class
        class_name = cls.__name__

        # Load from module-level registries
        # Note: We need to iterate through all registrations since we can't access the registry directly
        # The decorator module provides access functions, but for __init_subclass__ we need to
        # check all possible question names. For now, we'll rely on pending registries and
        # attribute scanning, which is the primary mechanism.

        # Load from pending registries (for functions decorated before class definition)
        pending_validators = RegistryManager.get_pending(
            "pending_input_validators", class_name
        )
        for question_name, func in pending_validators.items():
            cls._input_validators[question_name] = func

        pending_handlers = RegistryManager.get_pending(
            "pending_input_handlers", class_name
        )
        for question_name, func in pending_handlers.items():
            cls._input_handlers[question_name] = func

        pending_overrides = RegistryManager.get_pending(
            "pending_input_directive_overrides", class_name
        )
        for question_name, func in pending_overrides.items():
            cls._input_directive_overrides[question_name] = func

        # Register module-level input_context_provider and branch_function (defined before class)
        module = sys.modules.get(cls.__module__)
        flush_module_registrations_for_class(class_name, module)

        # Clear pending registrations for this class (module_name for input_review_override)
        clear_pending_registrations(class_name, cls.__module__)

        # Also scan class attributes for decorated functions (class methods)
        for attr_name in dir(cls):
            attr = getattr(cls, attr_name, None)
            if callable(attr) and hasattr(attr, "_interview_question_name"):
                question_name = attr._interview_question_name
                handler_type = getattr(attr, "_interview_handler_type", None)

                if handler_type == "input_handler":
                    cls._input_handlers[question_name] = attr
                elif handler_type == "input_validator" and question_name:
                    cls._input_validators[question_name] = attr
                elif handler_type == "input_directive_override" and question_name:
                    cls._input_directive_overrides[question_name] = attr
                elif handler_type == "input_review_override":
                    cls._input_review_override = attr
                elif handler_type == "branch_function" and question_name:
                    # Register branch function into module-level registry so it can
                    # be looked up by QuestionBranchEvaluator using the interview_type.
                    try:
                        RegistryManager.register_branch_function(
                            class_name, question_name, attr
                        )
                    except Exception:
                        logger.exception(
                            f"Failed to register branch_function '{question_name}' for '{class_name}'"
                        )
                elif handler_type == "input_context_provider" and question_name:
                    try:
                        RegistryManager.register_input_context_provider(
                            class_name, question_name, attr
                        )
                    except Exception:
                        logger.exception(
                            f"Failed to register input_context_provider '{question_name}' for '{class_name}'"
                        )

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

    @staticmethod
    def get_cancelled_handler(interview_type: str) -> Optional[Callable]:
        """Get cancellation handler for an interview type.

        Args:
            interview_type: Class name of the InterviewInteractAction

        Returns:
            Cancellation handler function if found, None otherwise
        """
        return _get_cancelled_handler(interview_type)

    @staticmethod
    def get_review_handler(interview_type: str) -> Optional[Callable]:
        """Get review handler for an interview type.

        Args:
            interview_type: Class name of the InterviewInteractAction

        Returns:
            Review handler function if found, None otherwise
        """
        return _get_review_handler(interview_type)

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

    @classmethod
    def get_input_review_override(cls) -> Optional[Callable]:
        """Get input review override for this interview action (from decorator registry).

        Returns:
            The registered override function if found, None otherwise.
        """
        override = cls._input_review_override
        if not override:
            override = _get_input_review_override(cls.__name__)
            if override:
                cls._input_review_override = override
        return override

    def _merge_standard_anchors(self) -> None:
        """Merge standard interview anchors with current anchors attribute.

        This method ensures standard anchors are always included, even when
        anchors are overridden in agent.yaml. Should be called from on_register()
        and on_reload() to handle runtime configuration changes.

        Standard anchors are contextualized with the class name to help distinguish
        multiple interview instances coexisting in a single agent.

        Subclasses may override _standard_interview_anchor_templates to customize
        or suppress standard anchors (e.g., set to [] for implementation-specific only).

        ADR-0009 / Wave 9e: the user-configured (entry-only) anchor list
        is preserved on ``self._entry_anchors`` so :meth:`get_anchors`
        can return only entry anchors when no active session exists.
        Mid-flight state-derived anchors confused first-entry routing when
        included in the entry catalog — narrowing to entry anchors restores
        clean intent matching for InteractRouter and the Orchestrator
        relevance gate.
        """
        # Get current anchors value (may be from agent.yaml override)
        current_anchors = getattr(self, "anchors", [])
        if not isinstance(current_anchors, list):
            current_anchors = []

        # Stash user-configured anchors BEFORE the standard merge so
        # state-aware ``get_anchors`` can return only the entry subset.
        self._entry_anchors = list(current_anchors)

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

    async def get_anchors(
        self, conversation: Optional[Any] = None
    ) -> Optional[List[str]]:
        """State-aware anchor surface (ADR-0009 / Wave 9e).

        Returns:
        - When an ACTIVE / REVIEW session exists on ``conversation``:
          the full merged list (entry + state-derived anchors) so
          InteractRouter can recognise mid-flight utterances like
          ``"User answers SignupInterviewInteractAction question"``.
        - Otherwise (no active session yet, or terminated): only the
          user-configured entry anchors. State-derived anchors are
          filtered out so first-entry routing sees a clean intent surface.

        Falls back to ``None`` (meaning "use static ``self.anchors``")
        on any error so a broken session lookup never silently strips
        the catalog for a real interview that's mid-flight.
        """
        terminal_values = {
            InterviewState.COMPLETED.value,
            InterviewState.CANCELLED.value,
        }
        entry_anchors = list(getattr(self, "_entry_anchors", []) or [])
        try:
            if conversation is None:
                return entry_anchors or None
            active_session = await conversation.node(
                node=[{"InterviewSession": {"state": {"$nin": list(terminal_values)}}}],
                interview_type=self.get_class_name(),
            )
            if active_session is None:
                # No active session → return entry-only.
                return entry_anchors or None
            # Active session → expose full catalog so mid-flight
            # responses are recognisable.
            return None
        except Exception as exc:
            logger.debug(
                "%s.get_anchors: session lookup failed: %s — "
                "falling back to static anchors",
                self.get_class_name(),
                exc,
            )
            return None

    def get_state_event_message(self, state: str) -> str:
        """Get formatted state event message for the current interview.

        Args:
            state: Interview state (ACTIVE, REVIEW, COMPLETED, CANCELLED)

        Returns:
            Formatted event message string
        """
        from .core.foundation.prompts import get_state_event_message

        return get_state_event_message(state, self.get_class_name())

    async def _get_question_node(
        self, field: str, session: InterviewSession
    ) -> Optional[QuestionNode]:
        """Get QuestionNode by ID.

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

        # Question nodes are not connected directly to InterviewInteractAction, need direct ref
        question_node = await QuestionNode.find_one(
            agent_id=self.agent_id, interview_type=self.get_class_name(), label=field
        )

        if not question_node:
            # Create on-demand if not found (shouldn't happen in normal flow)
            question_node = await QuestionNode.create(
                agent_id=self.agent_id,
                interview_type=self.get_class_name(),
                state=question_config,
                label=field,
            )
            await self.connect(question_node)

        # Cache the node
        if question_node:
            cache.set(field, question_node.id)

        return question_node

    async def get_state_node(self, state_type: InterviewState) -> Optional[StateNode]:
        """Get StateNode by state type (REVIEW, COMPLETED, CANCELLED).

        Args:
            state_type: The InterviewState type to find

        Returns:
            StateNode if found, None otherwise
        """
        return await self.node(node=StateNode, state_type=state_type)

    async def _queue_directive(self, visitor: "InteractWalker", directive: str) -> None:
        """Queue a directive for later response generation.

        Delegates to DirectiveBuilder.

        Args:
            visitor: InteractWalker
            directive: Directive string to queue
        """
        await self.directive_builder.queue_directive(visitor, directive)

    async def _get_first_question_node(
        self, session: InterviewSession
    ) -> Optional[QuestionNode]:
        """Get first question node via graph topology.

        Returns the QuestionNode with no incoming QuestionEdges (entry point).
        Uses question_graph[0] as primary source of truth (canonical flow order).
        Falls back to topology lookup if question_graph resolution fails.

        Args:
            session: Interview session

        Returns:
            First QuestionNode if found, None otherwise
        """
        if session.question_graph:
            first_name = session.question_graph[0].get("name")
            if first_name:
                node = await self._get_question_node(first_name, session)
                if node:
                    return node
        question_node = await self.node(node=QuestionNode)
        return question_node

    @property
    def _target_resolver(self) -> TargetResolver:
        """Lazy-instantiated TargetResolver for target node resolution."""
        if not hasattr(self, "_target_resolver_instance"):
            self._target_resolver_instance = TargetResolver(self)
        return self._target_resolver_instance

    async def _resolve_target_node(
        self,
        session: InterviewSession,
        intent: Intent,
        visitor: Optional["InteractWalker"] = None,
    ) -> None:
        """Determine and set session.target_node based on intent, state, and progress.

        Delegates to TargetResolver for resolution logic.

        Args:
            session: Interview session
            intent: Detected user intent
            visitor: Optional InteractWalker for branch function evaluation
        """
        await self._target_resolver.resolve(session, intent, visitor)

    def _get_question_graph(self) -> List[Dict[str, Any]]:
        """Get question graph.

        Returns:
            List of question configuration dictionaries
        """
        return self.question_graph

    def _build_and_apply_update_queue(
        self,
        session: InterviewSession,
        updates: Dict[str, Any],
        merge_existing: bool,
    ) -> bool:
        """Build update queue entries from updates dict and apply to session.

        Sets session responses, appends or merges into update_queue by graph order,
        and invalidates branch cache when merge_existing is True.

        Args:
            session: Interview session
            updates: Dict of field -> value to apply
            merge_existing: If True, merge with existing queue (replace by field)
                           and invalidate branch cache. If False, append only.

        Returns:
            True if any updates were applied, False otherwise
        """
        if not updates:
            return False

        graph_order = get_graph_order(session.question_graph)
        sorted_fields = sorted(updates.keys(), key=lambda f: graph_order.get(f, 999))

        queue_entries = []
        for field in sorted_fields:
            old_value = session.get_response(field)
            session.set_response(field, updates[field])
            queue_entries.append(
                {"field": field, "value": updates[field], "old_value": old_value}
            )

        if merge_existing:
            existing_fields = {e["field"] for e in session.update_queue}
            for entry in queue_entries:
                if entry["field"] in existing_fields:
                    session.update_queue = [
                        entry if e["field"] == entry["field"] else e
                        for e in session.update_queue
                    ]
                else:
                    session.update_queue.append(entry)
            session.update_queue.sort(key=lambda e: graph_order.get(e["field"], 999))
            if session.update_queue:
                BranchCache(session).invalidate_from(
                    session.update_queue[0]["field"], session.question_graph
                )
        else:
            for entry in queue_entries:
                session.update_queue.append(entry)

        return True

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
            node=[
                {
                    "InterviewSession": {
                        "state": {
                            "$nin": [
                                InterviewState.COMPLETED.value,
                                InterviewState.CANCELLED.value,
                            ]
                        }
                    }
                }
            ],
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
                auto_confirm=self.auto_confirm,
            )
            session.started_at = await self.now()
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
            logger.warning(
                f"{self.get_class_name()}: question_graph is empty. Define questions in subclass or agent.yaml"
            )

        # Validate graph structure
        from .core.graph.graph_validator import QuestionGraphValidator

        validator = QuestionGraphValidator(
            question_graph, interview_type=self.__class__.__name__
        )
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
        await self.question_builder.build_question_graph()

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
            await self.question_builder.build_question_graph()

    async def execute(self, visitor: "InteractWalker") -> None:
        """Execute interview action using target-node architecture.

        Flow:
        1. Load or create session
        2. Classify and extract responses from utterance
        3. Store extracted responses based on intent
        4. Determine target node based on intent, state, and completion
        5. Spawn walker on target node to traverse and collect directives
        6. Queue accumulated directives

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

        # 1. Get or create session for this conversation
        session = await self._get_or_create_session(conversation)
        visitor.interview_session = session

        try:
            # Reset directive builder task tracking for this execution
            self.directive_builder.reset_task_tracking()

            # Get utterance
            utterance = visitor.utterance if visitor.utterance else ""

            # 2. Classify and extract
            classification_result = await self.classifier.classify_and_extract(
                session, utterance, interaction, visitor
            )
            try:
                intent = Intent(classification_result.intent)
            except ValueError:
                intent = Intent.NONE

            # 3. Store extracted responses based on intent
            # Note: DECLINE is intentionally not handled here - QuestionNode handles
            # DECLINE logic (N/A for optional, directive for required) during traversal
            had_updates = False
            if intent == Intent.SUBMISSION and classification_result.extracted_data:
                # Route SUBMISSION values through update_queue to ensure validation pipeline runs
                had_updates = self._build_and_apply_update_queue(
                    session, classification_result.extracted_data, merge_existing=False
                )
            elif intent == Intent.UPDATE:
                # Collect updates from extracted_data (multi-field) or field/value (single-field)
                updates = {}
                if classification_result.extracted_data:
                    updates = classification_result.extracted_data
                elif classification_result.field:
                    updates = {classification_result.field: classification_result.value}

                if updates:
                    had_updates = self._build_and_apply_update_queue(
                        session, updates, merge_existing=True
                    )

            # 4. Determine target node based on intent and state
            await self._resolve_target_node(session, intent, visitor)
            target_node_id = session.target_node
            try:
                target_node = await Node.get(target_node_id)
            except Exception as exc:
                logger.exception(
                    f"{self.get_class_name()}: Failed to load target node {target_node_id}: {exc}"
                )
                session.target_node = None
                await session.save()
                raise

            interview_walker = InterviewWalker(
                interview_session=session,
                interaction=interaction,
                interact_visitor=visitor,
                interview_action=self,
                current_intent=intent,
            )

            await interview_walker.spawn(target_node)

            for directive in interview_walker.directives:
                await self._queue_directive(visitor, directive)

            # Post-walk: delegate graph sync and cleanup to QuestionPathWalker.sync when path may have changed.
            # Skip when we reached REVIEW (on_state_node already ran sync before building directive).
            # Skip when session was removed by a terminal state (COMPLETED/CANCELLED).
            terminal = interview_walker.terminal_state
            session_removed = terminal in (
                InterviewState.COMPLETED,
                InterviewState.CANCELLED,
            )

            if not session_removed:
                if (had_updates or session.update_queue) and (
                    terminal != InterviewState.REVIEW
                ):
                    from .core.graph.question_path_walker import QuestionPathWalker

                    first_node = await self._get_first_question_node(session)
                    if first_node:
                        await QuestionPathWalker.sync(
                            session,
                            first_node,
                            visitor,
                            self,
                            invalidate_cache=(intent == Intent.UPDATE),
                        )
        finally:
            # Always persist session state before Lambda function returns (critical for Lambda environments)
            # Lambda freezes the Python process after handler returns, so any deferred saves will be lost
            await session.save()

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
            with_utterance=with_utterance,
            with_response=with_response,
            with_interpretation=with_interpretation,
            with_event=with_event,
            formatted=True,
            max_statement_length=max_statement_length,
        )

        return history if history else []
