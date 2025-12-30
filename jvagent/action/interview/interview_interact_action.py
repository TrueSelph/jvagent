"""Interview Action Implementation

Root orchestrator for the interview state machine system.
Manages state transitions and coordinates all state-specific actions.

This is an abstract base class that should be extended to create concrete
interview implementations. Each subclass should define its own question_index
with the questions for that interview flow.
"""

import logging
from abc import ABC
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from jvagent.action.interact.base import InteractAction
from jvspatial.core.annotations import attribute

from .core.interview_session import InterviewSession
from .core.question_node import QuestionNode
from .core.validation import InterviewState
from .states.active_state import ActiveStateInteractAction
from .states.review_state import ReviewStateInteractAction
from .states.completed_state import CompletedStateInteractAction
from .states.cancelled_state import CancelledStateInteractAction

if TYPE_CHECKING:
    from jvagent.action.interview.core.interview_session import InterviewSession
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)

# Module-level registry for completion handlers (keyed by interview_type)
# This is populated when @on_interview_complete decorated functions are defined
_completion_handlers: Dict[str, Callable] = {}


def input_handler(question_name: str):
    """Decorator to register an input handler for a specific question.
    
    Input handlers process raw user input before validation (e.g., normalize time expressions).
    
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
        return func
    return decorator


def input_validator(question_name: str):
    """Decorator to register a validator for a specific question.
    
    Validators validate responses with custom logic.
    
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
        return func
    return decorator


def on_interview_complete(interview_type: str):
    """Decorator to register a completion handler for a specific interview type.
    
    Completion handlers are called when an interview session reaches the COMPLETED state.
    Use this to process collected data, trigger downstream actions, or perform cleanup.
    
    Args:
        interview_type: Class name of the InterviewInteractAction (e.g., 'SignupInterviewInteractAction')
        
    Handler Signature:
        The handler must accept two parameters:
        - session: InterviewSession - The completed interview session with all collected responses
        - visitor: InteractWalker - The walker for accessing context and responding
        
    Example:
        @on_interview_complete('SignupInterviewInteractAction')
        async def handle_signup_completion(
            session: InterviewSession,
            visitor: InteractWalker
        ) -> None:
            # Process collected data
            user_name = session.responses.get('user_name')
            user_email = session.responses.get('user_email')
            # Trigger downstream actions, send notifications, etc.
    """
    def decorator(func: Callable) -> Callable:
        # Register the handler in the module-level registry
        _completion_handlers[interview_type] = func
        logger.debug(f"Registered completion handler for interview type '{interview_type}'")
        return func
    return decorator


class InterviewInteractAction(InteractAction, ABC):
    """Root orchestrator for interview state machine.
    
    This action:
    1. Creates and connects all state-specific actions
    2. Creates and chains QuestionNode instances
    3. Routes to state actions based on session state
    
    Attributes:
        question_index: List of question configurations defining the interview schema
        
    Decorator Support:
        Use @input_handler('question_name') and @input_validator('question_name') decorators
        to register handlers and validators instead of embedding them in question_index.
    """
    
    description: str = "Root orchestrator for interview state machine system"
    
    # Class-level registries for decorator-registered handlers and validators
    # These are populated when the class is defined via decorators
    _input_handlers: Dict[str, Callable] = {}
    _input_validators: Dict[str, Callable] = {}
    
    def __init_subclass__(cls, **kwargs):
        """Initialize subclass and collect decorator-registered handlers/validators."""
        super().__init_subclass__(**kwargs)
        
        # Collect handlers and validators from class methods/attributes
        cls._input_handlers = {}
        cls._input_validators = {}
        
        # Scan class attributes for decorated functions
        for attr_name in dir(cls):
            attr = getattr(cls, attr_name, None)
            if callable(attr) and hasattr(attr, '_interview_question_name'):
                question_name = attr._interview_question_name
                handler_type = getattr(attr, '_interview_handler_type', None)
                
                if handler_type == "input_handler":
                    cls._input_handlers[question_name] = attr
                    logger.debug(f"{cls.__name__}: Registered input_handler '{attr_name}' for question '{question_name}'")
                elif handler_type == "input_validator":
                    cls._input_validators[question_name] = attr
                    logger.debug(f"{cls.__name__}: Registered input_validator '{attr_name}' for question '{question_name}'")
    
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
        
        Args:
            question_name: Name of the question
            
        Returns:
            Input validator function if found, None otherwise
        """
        return cls._input_validators.get(question_name)
    
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

    async def on_register(self) -> None:
        """Register the action and connect all state actions and question nodes.
        
        State connections match the state diagram flow:
        - InterviewInteractAction → ACTIVE (entry point)
        - ACTIVE → REVIEW (when questions complete)
        - ACTIVE → CANCELLED (when user cancels)
        - REVIEW → COMPLETED (when user confirms)
        - REVIEW → ACTIVE (when user edits - bidirectional)
        - REVIEW → CANCELLED (when user cancels)
        
        Note: Errors are automatically logged by the base Action class.
        """
        logger.info(f"{self.get_class_name()} on_register")
        
        # Validate question_index is defined
        if not self.question_index:
            logger.warning(f"{self.get_class_name()}: question_index is empty. Define questions in subclass or agent.yaml")
        
        # Create state actions
        active_action = await ActiveStateInteractAction.create(agent_id=self.agent_id)
        review_action = await ReviewStateInteractAction.create(agent_id=self.agent_id)
        completed_action = await CompletedStateInteractAction.create(agent_id=self.agent_id)
        cancelled_action = await CancelledStateInteractAction.create(agent_id=self.agent_id)
        
        # Connect state actions according to state diagram flow:
        # InterviewInteractAction → ACTIVE (entry point)
        await self.connect(active_action)
        
        # ACTIVE → REVIEW (when questions complete)
        await active_action.connect(review_action)
        
        # ACTIVE → CANCELLED (when user cancels)
        await active_action.connect(cancelled_action)
        
        # REVIEW → COMPLETED (when user confirms)
        await review_action.connect(completed_action)
        
        # REVIEW → ACTIVE (when user edits - bidirectional for flexibility)
        await review_action.connect(active_action, direction="both")
        
        # REVIEW → CANCELLED (when user cancels)
        await review_action.connect(cancelled_action)
        
        # Build QuestionNode chain
        await self._build_question_nodes(active_action)

    async def on_reload(self) -> None:
        """Reload the action - rebuild question nodes if question_index changed."""
        logger.info(f"{self.get_class_name()} on_reload")
        
        # Get current question node labels to detect changes
        active_action = await self.node(node="ActiveStateInteractAction")
        if active_action:
            existing_nodes = await active_action.nodes(direction="out", node=QuestionNode)
            existing_labels = {n.label for n in existing_nodes}
            
            # Get expected labels from question_index
            expected_labels = {q.get("name", "") for q in self.question_index if q.get("name")}
            
            # If labels changed, rebuild question nodes
            if existing_labels != expected_labels:
                logger.info(f"{self.get_class_name()}: question_index changed, rebuilding question nodes")
                # Disconnect and delete old question nodes
                for node in existing_nodes:
                    await active_action.disconnect(node)
                    await node.delete()
                # Rebuild
                await self._build_question_nodes(active_action)
        else:
            # If no active action, do full registration
            await self.on_register()
    
    async def _build_question_nodes(self, active_action: "ActiveStateInteractAction") -> None:
        """Build QuestionNode chain from question_index."""
        question_nodes = []
        for question_config in self.question_index:
            question_name = question_config.get("name", "")
            if not question_name:
                continue
            
            question_node = await QuestionNode.create(
                agent_id=self.agent_id,
                state=question_config,
                label=question_name,
            )
            question_nodes.append(question_node)
            await active_action.connect(question_node)
            
            # Chain question nodes together
            if len(question_nodes) > 1:
                await question_nodes[-2].connect(question_node)
        
        logger.info(f"{self.get_class_name()}: Built {len(question_nodes)} question nodes")

    async def execute(self, visitor: "InteractWalker") -> None:
        """Execute interview action - load or create session for this interview type.
        
        Routes to the appropriate state action based on the current InterviewSession state.
        State actions will self-check session.state and execute accordingly.
        
        Args:
            visitor: The InteractWalker visiting this action
            
        Note: Errors are automatically logged by InteractWalker. This method can add
        additional context-specific logging if needed.
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
        interview_type = self.get_class_name()  # e.g., "RegistrationInterviewAction"
        
        # Query conversation for active session of this interview type (filtered for efficiency)
        # Use .node() since we expect at most one active session per interview type per user
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
                interview_type=interview_type,  # Store type for filtering
                question_index=self.question_index,
                state=InterviewState.ACTIVE,
            )
            session.started_at = datetime.now()
            await session.save()
            
            # Attach to conversation (primary and only attachment)
            await conversation.connect(session)
            
            logger.debug(f"{interview_type}: Created new session {session.id}")
        
        # Store session on visitor for state actions to access
        visitor.interview_session = session
        
        # Get connected state actions and add them to walk path
        # They will self-check session.state and execute accordingly
        state_actions = await self.nodes(node=InteractAction)
        if state_actions:
            await visitor.add_next(state_actions)
