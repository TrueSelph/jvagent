"""Interview service for orchestrating interview components.

This module provides a service layer that coordinates between different
interview components (classification, state handling, response processing, etc.).
"""

import logging
from typing import TYPE_CHECKING, Any, Optional

from .classification import InterviewClassifier as ClassificationService
from .interview_session import InterviewSession
from .question_builder import QuestionBuilder
from .response_processor import ResponseProcessor
from .state_handlers import StateHandler
from .state_machine import InterviewStateMachine
from .enums import InterviewState, Intent

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.action.interview.interview_interact_action import ClassificationResult, InterviewInteractAction
    from jvagent.memory import Interaction

logger = logging.getLogger(__name__)


class InterviewService:
    """Service class that orchestrates interview components.
    
    This class provides a unified interface for coordinating between
    classification, state handling, response processing, and question building.
    """
    
    def __init__(self, action: "InterviewInteractAction"):
        """Initialize interview service with action instance.
        
        Args:
            action: InterviewInteractAction instance
        """
        self.action = action
        self._classifier = None
        self._state_handler = None
        self._response_processor = None
        self._question_builder = None
    
    @property
    def classifier(self) -> ClassificationService:
        """Get or create classification service."""
        if self._classifier is None:
            self._classifier = ClassificationService(self.action)
        return self._classifier
    
    @property
    def state_handler(self) -> StateHandler:
        """Get or create state handler."""
        if self._state_handler is None:
            self._state_handler = StateHandler(self.action)
        return self._state_handler
    
    @property
    def response_processor(self) -> ResponseProcessor:
        """Get or create response processor."""
        if self._response_processor is None:
            self._response_processor = ResponseProcessor(self.action)
        return self._response_processor
    
    @property
    def question_builder(self) -> QuestionBuilder:
        """Get or create question builder."""
        if self._question_builder is None:
            self._question_builder = QuestionBuilder(self.action)
        return self._question_builder
    
    async def classify_and_extract(
        self,
        session: InterviewSession,
        utterance: str,
        interaction: "Interaction",
        visitor: "InteractWalker"
    ) -> "ClassificationResult":
        """Classify user intent and extract field values.
        
        Args:
            session: Interview session
            utterance: User's utterance
            interaction: Current interaction
            visitor: InteractWalker
            
        Returns:
            ClassificationResult with intent and extracted data
        """
        return await self.classifier.classify_and_extract(
            session, utterance, interaction, visitor
        )
    
    async def build_question_nodes(self) -> None:
        """Build QuestionNode and StateNode graph from question_graph."""
        await self.question_builder.build_question_nodes()
    
    async def generate_directive(
        self,
        session: InterviewSession,
        classification_result: "ClassificationResult",
        visitor: "InteractWalker",
        interaction: "Interaction"
    ) -> None:
        """Generate and send directive based on session state and classification result.

        Args:
            session: Interview session
            classification_result: Result from classification routine
            visitor: InteractWalker
            interaction: Current interaction
        """
        # Create state machine for transition management
        state_machine = InterviewStateMachine(session)
        
        # Handle high-priority intents that cause immediate state transitions
        # CANCELLATION: Highest priority - can occur in any state
        if classification_result.intent == Intent.CANCELLATION and session.state != InterviewState.CANCELLED:
            try:
                state_machine.transition_to(InterviewState.CANCELLED, reason="User cancellation")
                await session.save()
            except ValueError as e:
                logger.error(f"{self.action.get_class_name()}: {e}", exc_info=True)
            except Exception as e:
                logger.error(f"{self.action.get_class_name()}: Failed to transition to CANCELLED: {e}", exc_info=True)

        # CONFIRMATION: Only valid in REVIEW state - transition to COMPLETED immediately
        if classification_result.intent == Intent.CONFIRMATION and session.state == InterviewState.REVIEW:
            try:
                state_machine.transition_to(InterviewState.COMPLETED, reason="User confirmation")
                await session.save()
                await self.state_handler.generate_completed_directive(session, visitor)
                return  # Exit early - completion handled in same turn
            except ValueError as e:
                logger.error(f"{self.action.get_class_name()}: {e}", exc_info=True)
            except Exception as e:
                logger.error(f"{self.action.get_class_name()}: Failed to transition to COMPLETED: {e}", exc_info=True)

        # Route to state-specific handlers (pass state_machine for transitions)
        if session.state == InterviewState.ACTIVE:
            await self.state_handler.generate_active_directive(session, classification_result, visitor, interaction, state_machine)
            # Handle cascading transition: ACTIVE -> REVIEW (when all questions answered)
            if session.state == InterviewState.REVIEW:
                await self.state_handler.generate_review_directive(session, classification_result, visitor, state_machine)
                # Handle cascading transition: REVIEW -> COMPLETED (if CONFIRMATION was missed)
                if session.state == InterviewState.COMPLETED:
                    await self.state_handler.generate_completed_directive(session, visitor)

        elif session.state == InterviewState.REVIEW:
            # Handle REVIEW state (UPDATE intent, unclear responses, first-time summary display)
            await self.state_handler.generate_review_directive(session, classification_result, visitor, state_machine)

        elif session.state == InterviewState.COMPLETED:
            # Handle already-completed state (e.g., from previous interaction)
            await self.state_handler.generate_completed_directive(session, visitor)

        elif session.state == InterviewState.CANCELLED:
            # Handle cancelled state (cleanup and message)
            await self.state_handler.generate_cancelled_directive(session, visitor)

        else:
            logger.warning(f"{self.action.get_class_name()}: Unknown session state: {session.state}")
