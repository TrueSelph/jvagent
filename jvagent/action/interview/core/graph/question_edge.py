"""QuestionEdge for conditional question traversal.

This module provides QuestionEdge, a specialized edge that stores condition
metadata for conditional branching in interview question trees. The edge
encapsulates branch condition evaluation and target resolution (object-spatial).
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.core import Edge, Node
from jvspatial.core.annotations import attribute

from ..utils.cache_utils import BranchCache
from .question_branch_evaluator import QuestionBranchEvaluator

if TYPE_CHECKING:
    from ..session.interview_session import InterviewSession

logger = logging.getLogger(__name__)


class QuestionEdge(Edge):
    """Edge connecting QuestionNodes with optional condition metadata.

    QuestionEdge extends Edge to store condition information that determines
    when this edge should be traversed based on previous question responses.
    The edge owns evaluation (via QuestionBranchEvaluator / branch functions)
    and returns its target node when the condition is satisfied (or when
    it is a default edge with no condition).

    Attributes:
        condition: Optional condition dict with 'op' and optional 'value' keys
        branch_index: Position in the source question's branches list; -1 for default/sequential edges
        is_default: True for default_next or sequential fallback edges (no condition)
    """

    condition: Optional[Dict[str, Any]] = attribute(
        default=None,
        description="Condition dict for conditional traversal (e.g., {'op': 'equals', 'value': 'premium'})",
    )

    branch_index: Optional[int] = attribute(
        default=None,
        description="Position in source question's branches list; -1 for default",
    )

    is_default: bool = attribute(
        default=False, description="True for default_next / sequential fallback edges"
    )

    @classmethod
    def sort_by_priority(cls, edges: List["QuestionEdge"]) -> List["QuestionEdge"]:
        """Sort edges by priority: conditional edges first (by branch_index), then defaults.

        This ensures that conditional branches are evaluated before default branches,
        and within each category, edges are ordered by their branch_index.

        Args:
            edges: List of QuestionEdge instances to sort

        Returns:
            Sorted list of edges
        """
        return sorted(
            edges,
            key=lambda e: (
                0 if e.condition else 1,
                e.branch_index if e.branch_index is not None else 999,
            ),
        )

    async def _get_target_node(self) -> Optional[Node]:
        """Resolve this edge's target node ID to a Node instance.

        Returns:
            The target Node if resolution succeeds, None otherwise.
        """
        target_id = getattr(self, "target", None)
        if not target_id:
            return None
        try:
            return await Node.get(target_id)
        except Exception as e:
            logger.warning(
                f"QuestionEdge: Failed to resolve target node {target_id}: {e}"
            )
            return None

    async def evaluate(
        self,
        session: "InterviewSession",
        implicit_question: str,
        visitor: Optional[Any] = None,
        interview_action: Optional[Any] = None,
        edge_count: int = 1,
    ) -> Optional[Node]:
        """Evaluate this edge: return the target node if the branch holds or edge is simple.

        For edges with no condition, returns the target node. For conditional edges,
        checks BranchCache for a cached target for this question; if it equals this
        edge's target label, returns the target node. Otherwise runs
        QuestionBranchEvaluator; if the condition matches, records the branch path
        and returns the target node.

        Branch path is only recorded when edge_count > 1 (actual branch point).

        Args:
            session: Interview session for condition evaluation
            implicit_question: Question name that owns this branch (implicit from context)
            visitor: Optional InteractWalker for branch function access
            edge_count: Number of outgoing edges; only record in cache when > 1

        Returns:
            Target Node if this edge should be taken, None otherwise
        """
        target = await self._get_target_node()
        if target is None:
            return None

        if not self.condition:
            if edge_count > 1:
                self.record_branch_path(session, implicit_question, target)
            return target

        branch_cache = BranchCache(session)
        cached_target = branch_cache.get(implicit_question)
        if cached_target is not None:
            if cached_target == target.label:
                if edge_count > 1:
                    self.record_branch_path(session, implicit_question, target)
                return target
            return None

        if not await QuestionBranchEvaluator.matches(
            self.condition,
            session,
            implicit_question=implicit_question,
            visitor=visitor,
            interview_action=interview_action,
        ):
            return None
        if edge_count > 1:
            self.record_branch_path(session, implicit_question, target)
        return target

    def record_branch_path(
        self,
        session: "InterviewSession",
        source_question_name: str,
        target_node: Node,
    ) -> None:
        """Record the branch path taken for path-change detection.

        Delegates to BranchCache using this edge's metadata.

        Args:
            session: Interview session to attach cache to
            source_question_name: Name of the question that owns this branch
            target_node: The target node (used for target label)
        """
        BranchCache(session).record_branch_path(
            source_question_name,
            self.branch_index if self.branch_index is not None else -1,
            target_node.label,
            is_default=self.is_default,
        )
