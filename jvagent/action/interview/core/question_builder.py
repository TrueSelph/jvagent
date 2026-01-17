"""Question builder for interview action.

This module handles building QuestionNode and StateNode graphs from question_graph configurations.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, List

from .question_edge import QuestionEdge
from .question_node import QuestionNode
from .question_walker import QuestionWalker

if TYPE_CHECKING:
    from jvagent.action.interview.interview_interact_action import InterviewInteractAction

logger = logging.getLogger(__name__)


class QuestionBuilder:
    """Builds QuestionNode and StateNode graphs from question_graph configurations."""
    
    def __init__(self, action: "InterviewInteractAction"):
        """Initialize question builder with action instance.
        
        Args:
            action: InterviewInteractAction instance
        """
        self.action = action
    
    async def build_question_nodes(self) -> None:
        """Build QuestionNode and StateNode graph from question_graph with conditional branches.

        Creates QuestionNodes and StateNodes and connects them based on branches configuration.
        Supports both linear (no branches) and tree-based (with branches) arrangements.
        """
        # Use the action's build method which handles StateNodes
        await self.action._build_question_nodes()
