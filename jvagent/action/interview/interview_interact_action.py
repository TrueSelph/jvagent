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
from .states.interview_state import InterviewStateInteractAction
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
        
        # Validate question_index is defined
        if not self.question_index:
            logger.warning(f"{self.get_class_name()}: question_index is empty. Define questions in subclass or agent.yaml")
        
        # Create state actions
        interview_action = await InterviewStateInteractAction.create(agent_id=self.agent_id)
        review_action = await ReviewStateInteractAction.create(agent_id=self.agent_id)
        completed_action = await CompletedStateInteractAction.create(agent_id=self.agent_id)
        cancelled_action = await CancelledStateInteractAction.create(agent_id=self.agent_id)
        
        # Connect state actions according to state diagram flow:
        # InterviewInteractAction → ACTIVE (entry point)
        await self.connect(interview_action)
        
        # ACTIVE → REVIEW (when questions complete)
        await interview_action.connect(review_action)
        
        # ACTIVE → CANCELLED (when user cancels)
        await interview_action.connect(cancelled_action)
        
        # REVIEW → COMPLETED (when user confirms)
        await review_action.connect(completed_action)
        
        # REVIEW → ACTIVE (when user edits - bidirectional for flexibility)
        await review_action.connect(interview_action, direction="both")
        
        # REVIEW → CANCELLED (when user cancels)
        await review_action.connect(cancelled_action)
        
        # Build QuestionNode chain
        await self._build_question_nodes(interview_action)

    async def on_reload(self) -> None:
        """Reload the action - rebuild question nodes if question_index changed."""
        
        # Get current question node labels to detect changes
        interview_action = await self.node(node="InterviewStateInteractAction")
        if interview_action:
            existing_nodes = await interview_action.nodes(direction="out", node=QuestionNode)
            existing_labels = {n.label for n in existing_nodes}
            
            # Get expected labels from question_index
            expected_labels = {q.get("name", "") for q in self.question_index if q.get("name")}
            
            # If labels changed, rebuild question nodes
            if existing_labels != expected_labels:
                # Disconnect and delete old question nodes
                for node in existing_nodes:
                    await interview_action.disconnect(node)
                    await node.delete()
                # Rebuild
                await self._build_question_nodes(interview_action)
        else:
            # If no active action, do full registration
            await self.on_register()
    
    async def _build_question_nodes(self, interview_action: "InterviewStateInteractAction") -> None:
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
            await interview_action.connect(question_node)
        
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
        
        # Store session on visitor for state actions to access
        visitor.interview_session = session
        
        # Route to the appropriate state action based on session state (state machine pattern)
        # Only add the state action that matches the current session state
        state_action = None
        
        if session.state == InterviewState.ACTIVE:
            state_action = await self.node(node=InterviewStateInteractAction)
        elif session.state == InterviewState.REVIEW:
            # Find ReviewStateInteractAction via InterviewStateInteractAction (it's connected there)
            interview_action = await self.node(node=InterviewStateInteractAction)
            if interview_action:
                state_action = await interview_action.node(node=ReviewStateInteractAction)
        elif session.state == InterviewState.COMPLETED:
            # Find CompletedStateInteractAction via ReviewStateInteractAction
            interview_action = await self.node(node=InterviewStateInteractAction)
            if interview_action:
                review_action = await interview_action.node(node=ReviewStateInteractAction)
                if review_action:
                    state_action = await review_action.node(node=CompletedStateInteractAction)
        elif session.state == InterviewState.CANCELLED:
            # Find CancelledStateInteractAction (connected to both InterviewStateInteractAction and ReviewStateInteractAction)
            interview_action = await self.node(node=InterviewStateInteractAction)
            if interview_action:
                state_action = await interview_action.node(node=CancelledStateInteractAction)
                if not state_action:
                    review_action = await interview_action.node(node=ReviewStateInteractAction)
                    if review_action:
                        state_action = await review_action.node(node=CancelledStateInteractAction)
        
        if state_action:
            await visitor.add_next([state_action])
        else:
            logger.warning(f"{self.get_class_name()}: No state action found for session state {session.state}")
