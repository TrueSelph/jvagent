"""Interview service for orchestrating interview components.

This module provides a service layer that coordinates between different
interview components (classification, state handling, response processing, etc.).

NOTE: The new target-node architecture in InterviewInteractAction.execute()
handles the main interview flow via QuestionWalker.traverse_from_target().
This service is now primarily used for:
- build_question_graph(): Building the QuestionNode/StateNode graph structure
- classify_and_extract(): Delegating to ClassificationHandler

The generate_directive() method is DEPRECATED - use the walker-based flow instead.
"""

import logging
from typing import TYPE_CHECKING, Any, Optional

from ..classification.classification_handler import ClassificationHandler as ClassificationService
from .interview_session import InterviewSession
from ..graph.question_graph_builder import QuestionGraphBuilder

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.action.interview.interview_interact_action import ClassificationResult, InterviewInteractAction
    from jvagent.memory import Interaction

logger = logging.getLogger(__name__)


class InterviewService:
    """Service class that orchestrates interview components.

    This class provides a unified interface for coordinating between
    classification and question building. The main interview flow is now
    handled by InterviewInteractAction.execute() using the walker pattern.
    """

    def __init__(self, action: "InterviewInteractAction"):
        """Initialize interview service with action instance.

        Args:
            action: InterviewInteractAction instance
        """
        self.action = action
        self._classifier = None
        self._question_builder = None

    @property
    def classifier(self) -> ClassificationService:
        """Get or create classification service."""
        if self._classifier is None:
            self._classifier = ClassificationService(self.action)
        return self._classifier

    @property
    def question_builder(self) -> QuestionGraphBuilder:
        """Get or create question graph builder."""
        if self._question_builder is None:
            self._question_builder = QuestionGraphBuilder(self.action)
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

    async def build_question_graph(self) -> None:
        """Build QuestionNode and StateNode graph from question_graph."""
        await self.question_builder.build_question_graph()
