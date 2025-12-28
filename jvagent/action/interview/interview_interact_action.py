"""Interview Action Implementation

Root orchestrator for the interview state machine system.
Manages state transitions and coordinates all state-specific actions.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvagent.action.interact.base import InteractAction
from jvspatial.core.annotations import attribute

from .core.interview_walker import InterviewWalker
from .core.question_node import QuestionNode
from .states.active_state import ActiveStateInteractAction
from .states.review_state import ReviewStateInteractAction
from .states.completed_state import CompletedStateInteractAction
from .states.cancelled_state import CancelledStateInteractAction

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)


class InterviewInteractAction(InteractAction):
    """Root orchestrator for interview state machine.
    
    This action:
    1. Creates and connects all state-specific actions
    2. Creates and chains QuestionNode instances
    3. Routes to InterviewWalker for state-based execution
    
    Attributes:
        state_index: List of question configurations defining the interview schema
    """
    
    description: str = "Root orchestrator for interview state machine system"
    
    weight: int = attribute(
        default=-40,
        description="Execution weight (runs after InteractRouter but before PersonaAction)",
    )
    
    always_execute: bool = attribute(
        default=False,
        description="Only execute when interview should be active",
    )
    
    state_index: List[Dict[str, Any]] = attribute(
        default=[
            {
                "name": "user_name",
                "question": "What's your full name?",
                "constraints": {
                    "description": "The user's full name",
                    "instructions": "The user's full name must include their first and last name.",
                    "type": "string",
                },
                "required": True
            },
            {
                "name": "available_times",
                "question": "What times are you available to train?",
                "constraints": {
                    "description": "The user's available times",
                    "type": "string"
                },
                "required": True
            },
            {
                "name": "user_email",
                "question": "What is your email?",
                "constraints": {
                    "description": "The user's email address",
                    "type": "string",
                    "format": "email"
                },
                "required": True
            },
        ],
        description="List of question configurations defining the interview schema",
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
        logger.info("InterviewInteractAction on_register")
        
        # Create state actions (InterviewInteractAction serves as entry point)
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
        
        # Build QuestionNode chain and connect to ActiveStateInteractAction
        question_nodes = []
        for question_config in self.state_index:
            question_name = question_config.get("name", "")
            if not question_name:
                continue
            
            question_node = await QuestionNode.create(
                agent_id=self.agent_id,
                state=question_config,
                label=question_name,
            )
            question_nodes.append(question_node)
            
            # Connect to active action
            await active_action.connect(question_node)
            
            # Chain question nodes together
            if len(question_nodes) > 1:
                await question_nodes[-2].connect(question_node)
        
        logger.info(
            f"InterviewInteractAction: Registered {len(question_nodes)} question nodes "
            f"and 4 state actions with connections matching state diagram flow"
        )

    async def on_reload(self) -> None:
        """Reload the action - same as on_register."""
        logger.info("InterviewInteractAction on_reload")
        await self.on_register()

    async def execute(self, visitor: "InteractWalker") -> None:
        """Execute interview action - route to appropriate state action.
        
        Routes to the appropriate state action based on the current InterviewSession state.
        Uses InterviewWalker for proper state-based routing.
        
        Args:
            visitor: The InteractWalker visiting this action
            
        Note: Errors are automatically logged by InteractWalker. This method can add
        additional context-specific logging if needed.
        """
        interaction = visitor.interaction
        if not interaction:
            logger.warning("InterviewInteractAction: No interaction available")
            return
        
        # Get conversation
        conversation = await interaction.get_conversation()
        if not conversation:
            logger.warning("InterviewInteractAction: No conversation available")
            return
        
        # Load or create session
        from .core.interview_session import InterviewSession
        from .core.validation import InterviewState
        
        session = None
        try:
            # Try to find existing session
            sessions = await conversation.nodes(direction="out", node=InterviewSession)
            if not sessions:
                sessions = await InterviewSession.find({
                    "context.conversation_id": conversation.id
                })
            
            if sessions:
                active_sessions = [s for s in sessions if s.state != InterviewState.COMPLETED and s.state != InterviewState.CANCELLED]
                if active_sessions:
                    session = active_sessions[0]
        except Exception:
            # Continue to create new session - error will be logged by base system if it propagates
            pass
        
        # Create new session if none exists
        if not session:
            from datetime import datetime
            session = await InterviewSession.create(
                agent_id=self.agent_id,
                conversation_id=conversation.id,
                question_index=self.state_index,
                state=InterviewState.ACTIVE,
            )
            session.started_at = datetime.now()
            await session.save()
            await conversation.connect(session)
            logger.debug(f"InterviewInteractAction: Created new session {session.id}")
        
        # Handle legacy IDLE state
        if session.state == InterviewState.IDLE:
            session.transition_to(InterviewState.ACTIVE)
            if not session.started_at:
                from datetime import datetime
                session.started_at = datetime.now()
            await session.save()
        
        # Store session in visitor if it's an InterviewWalker (for QuestionNode traversal)
        if isinstance(visitor, InterviewWalker):
            visitor.interview_session = session
        
        # Route to appropriate state action - the walker will automatically visit connected actions
        # We don't need to explicitly visit here, as the InteractWalker will traverse connected
        # InteractActions automatically. We just need to ensure the session is available.
        logger.debug(f"InterviewInteractAction: Session in {session.state} state, walker will route automatically")
