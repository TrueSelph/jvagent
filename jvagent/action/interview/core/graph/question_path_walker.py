"""QuestionPathWalker for determining next target and reachable questions.

This module provides QuestionPathWalker, a lightweight walker that traverses
the question graph following the active branch path (using BranchCache) to:
- Find the next unanswered question on the active path
- Collect all reachable questions on the active path
- Determine if all reachable questions are answered (→ REVIEW)

Unlike PostUpdateWalker:
- Does NOT invalidate cache (uses existing branch decisions)
- Does NOT prune responses (read-only)
- Can stop at first unanswered question (optimization)
- Can operate in two modes: find_next or collect_all
"""

import logging
from typing import Any, Optional, Set

from pydantic import PrivateAttr
from jvspatial.core import Walker, on_visit

from .question_node import QuestionNode
from .state_node import StateNode

logger = logging.getLogger(__name__)


class QuestionPathWalker(Walker):
    """Lightweight walker for determining next target node and reachable questions.
    
    This walker traverses the question graph following the active branch path
    (using BranchCache) to:
    - Find the next unanswered question on the active path
    - Collect all reachable questions on the active path
    - Determine if all reachable questions are answered (→ REVIEW)
    
    Unlike PostUpdateWalker:
    - Does NOT invalidate cache (uses existing branch decisions)
    - Does NOT prune responses (read-only)
    - Stops at first unanswered question in find_next mode (optimization)
    - Can operate in two modes: find_next or collect_all
    
    Usage:
        # Find next unanswered question
        next_node = await QuestionPathWalker.find_next_target(session, first_node, visitor)
        
        # Get all reachable questions
        reachable = await QuestionPathWalker.get_reachable_questions(session, first_node, visitor)
    """

    interview_session: Optional[Any] = None
    interact_visitor: Optional[Any] = None
    interview_action: Optional[Any] = None
    
    # Mode: "find_next" stops at first unanswered, "collect_all" traverses entire path
    _mode: str = PrivateAttr(default="find_next")
    _next_target: Optional[QuestionNode] = PrivateAttr(default=None)
    _reachable: Set[str] = PrivateAttr(default_factory=set)
    _visited_ids: Set[str] = PrivateAttr(default_factory=set)

    @on_visit(QuestionNode)
    async def on_question_node(self, here: QuestionNode) -> None:
        """Handle visiting a QuestionNode during path traversal.
        
        Collects the question name as reachable and follows the active branch.
        In find_next mode, stops at the first unanswered question.
        
        Args:
            here: QuestionNode being visited
        """
        # Prevent loops
        if here.id in self._visited_ids:
            return
        self._visited_ids.add(here.id)
        
        question_name = here.state.get("name", here.label) if hasattr(here, "state") else here.label
        self._reachable.add(question_name)
        
        # Check if answered
        if question_name not in self.interview_session.responses:
            # Found unanswered question
            if self._mode == "find_next" and self._next_target is None:
                self._next_target = here
                return  # Stop traversal - we found what we're looking for
        
        # Question is answered, continue following edges
        from .question_edge import QuestionEdge
        
        edges = await here.edges(direction="out")
        ordered = QuestionEdge.sort_by_priority(edges)
        for edge in ordered:
            target = await edge.evaluate(
                self.interview_session, question_name, self.interact_visitor, self.interview_action
            )
            if target is not None:
                await self.visit(target)
                return

    @on_visit(StateNode)
    async def on_state_node(self, here: StateNode) -> None:
        """Handle visiting a StateNode - terminal node, stop traversal.
        
        Args:
            here: StateNode being visited
        """
        # Reached terminal state node - stop traversal
        pass

    @property
    def next_target(self) -> Optional[QuestionNode]:
        """Get the next unanswered question node found during traversal."""
        return self._next_target

    @property
    def reachable(self) -> Set[str]:
        """Get the set of question names reachable on the active branch path."""
        return self._reachable

    @classmethod
    async def find_next_target(
        cls,
        session: Any,
        first_node: Optional[QuestionNode] = None,
        visitor: Optional[Any] = None,
        interview_action: Optional[Any] = None
    ) -> Optional[QuestionNode]:
        """Find the next unanswered question on the active branch path.
        
        Traverses the question graph following conditional branches (using BranchCache)
        and stops at the first unanswered question encountered.
        
        Args:
            session: InterviewSession with responses and branch cache
            first_node: The first QuestionNode in the graph (entry point)
            visitor: Optional InteractWalker for branch function evaluation
        
        Returns:
            Next unanswered QuestionNode on the active path, or None if all answered
        """
        if first_node is None:
            logger.warning("QuestionPathWalker.find_next_target: first_node is None")
            return None
        
        walker = cls(
            interview_session=session,
            interact_visitor=visitor,
            interview_action=interview_action,
        )
        walker._mode = "find_next"
        
        try:
            await walker.spawn(first_node)
        except Exception:
            logger.exception("QuestionPathWalker.find_next_target: error during traversal")
            return None
        
        return walker.next_target

    @classmethod
    async def get_reachable_questions(
        cls,
        session: Any,
        first_node: Optional[QuestionNode] = None,
        visitor: Optional[Any] = None,
        interview_action: Optional[Any] = None
    ) -> Set[str]:
        """Get all reachable questions on the active branch path.
        
        Traverses the entire question graph following conditional branches
        (using BranchCache) and collects all question names encountered.
        
        Args:
            session: InterviewSession with responses and branch cache
            first_node: The first QuestionNode in the graph (entry point)
            visitor: Optional InteractWalker for branch function evaluation
        
        Returns:
            Set of question names reachable given current responses and branch decisions
        """
        if first_node is None:
            logger.warning("QuestionPathWalker.get_reachable_questions: first_node is None")
            return set()
        
        walker = cls(
            interview_session=session,
            interact_visitor=visitor,
            interview_action=interview_action,
        )
        walker._mode = "collect_all"
        
        try:
            await walker.spawn(first_node)
        except Exception:
            logger.exception("QuestionPathWalker.get_reachable_questions: error during traversal")
            return set()
        
        return walker.reachable
