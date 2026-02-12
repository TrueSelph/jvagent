"""PostUpdateWalker for post-update graph sync and session cleanup.

After the InterviewWalker finishes an update re-walk, this walker traverses
the spatial graph from the first QuestionNode to compute the full reachable
question set on the active branch path. It then prunes responses, validation
results, and update_queue entries for questions no longer on that path.

The walker is **cache-independent**: it invalidates the BranchCache before
traversal so every conditional edge is evaluated fresh via
QuestionBranchEvaluator. The cache is naturally re-populated by
record_branch_path() calls during edge evaluation, leaving it consistent
for subsequent InterviewWalker runs.

Uses @on_visit decorators for the walker-node pattern.

Responsibilities:
- Invalidate BranchCache to force fresh condition evaluation
- Compute reachable question set via spatial graph traversal
- Prune session.responses for unreachable questions
- Prune session.validation_results for unreachable questions
- Clean up update_queue entries for unreachable questions
- Record pruned responses via BranchCache.record_pruned_response()
"""

import logging
from typing import Any, Optional, Set

from pydantic import PrivateAttr
from jvspatial.core import Walker, on_visit

from .question_node import QuestionNode
from .state_node import StateNode
from ..utils.cache_utils import BranchCache

logger = logging.getLogger(__name__)


class PostUpdateWalker(Walker):
    """Walker that traverses the question graph to sync session state after updates.

    Unlike InterviewWalker, this walker does NOT interact with the user:
    - No directives, no validation, no prompts
    - Visits every reachable QuestionNode (does not stop at unanswered questions)
    - Invalidates the BranchCache before traversal for fresh condition evaluation
    - Follows edges via QuestionBranchEvaluator (cache is re-populated as a side-effect)
    - Collects the reachable question name set
    - After traversal, prunes stale session data

    Usage:
        await PostUpdateWalker.sync(session, first_node, interact_visitor)
    """

    interview_session: Optional[Any] = None
    interact_visitor: Optional[Any] = None
    _reachable: Set[str] = PrivateAttr(default_factory=set)
    _visited_ids: Set[str] = PrivateAttr(default_factory=set)

    @on_visit(QuestionNode)
    async def on_question_node(self, here: QuestionNode) -> None:
        """Collect reachable question name and follow the active edge."""
        if here.id in self._visited_ids:
            return
        self._visited_ids.add(here.id)

        question_name = here.state.get("name", here.label)
        self._reachable.add(question_name)

        # Follow the active outgoing edge (same ordering as InterviewWalker)
        from .question_edge import QuestionEdge
        
        edges = await here.edges(direction="out")
        ordered = QuestionEdge.sort_by_priority(edges)
        for edge in ordered:
            target = await edge.evaluate(
                self.interview_session, question_name, self.interact_visitor
            )
            if target is not None:
                await self.visit(target)
                return

    @on_visit(StateNode)
    async def on_state_node(self, here: StateNode) -> None:
        """Terminal node -- stop traversal on this branch."""
        pass

    @property
    def reachable(self) -> Set[str]:
        """Set of question names reachable on the active branch path."""
        return self._reachable

    def _prune_session(self) -> None:
        """Prune responses, validation_results, and update_queue for unreachable questions.

        Safety: if the reachable set is empty (e.g. traversal failed silently),
        pruning is skipped to prevent catastrophic data loss.
        """
        session = self.interview_session

        if not self._reachable:
            logger.warning(
                "PostUpdateWalker._prune_session: reachable set is empty — "
                "skipping pruning to prevent data loss"
            )
            return

        branch_cache = BranchCache(session)
        pruned_questions = []

        for field in list(session.responses.keys()):
            if field not in self._reachable:
                old_value = session.responses.pop(field)
                session.validation_results.pop(field, None)
                branch_cache.record_pruned_response(field, old_value, "branch_path_change")
                pruned_questions.append(field)
                logger.debug(f"PostUpdateWalker: pruned unreachable response '{field}'")

        if session.update_queue:
            session.update_queue = [
                e for e in session.update_queue if e["field"] in self._reachable
            ]

        if pruned_questions:
            logger.info(
                f"PostUpdateWalker: pruned {len(pruned_questions)} unreachable "
                f"response(s): {pruned_questions}"
            )

    @classmethod
    async def sync(
        cls,
        session: Any,
        first_node: Optional[Any] = None,
        interact_visitor: Optional[Any] = None,
    ) -> Set[str]:
        """Run post-update sync: walk the graph, prune stale data, return reachable set.

        Single entry point for callers.  Invalidates the BranchCache so every
        conditional edge is evaluated fresh, spawns the walker from the first
        question node, prunes unreachable session data, and saves.

        Args:
            session: InterviewSession with responses to prune.
            first_node: The first QuestionNode in the graph (entry point).
            interact_visitor: Optional InteractWalker for branch function evaluation.

        Returns:
            Set of reachable question names on the active path.
        """
        if first_node is None:
            logger.warning("PostUpdateWalker.sync: first_node is None, skipping traversal")
            return set()

        # Invalidate entire branch cache so edge evaluation is fresh.
        # The cache is re-populated by record_branch_path() during traversal.
        branch_cache = BranchCache(session)
        branch_cache.invalidate_all()
        branch_cache.clear_pruned_responses()

        walker = cls(
            interview_session=session,
            interact_visitor=interact_visitor,
        )

        try:
            await walker.spawn(first_node)
        except Exception:
            logger.exception(
                "PostUpdateWalker.sync: error during traversal — "
                "pruning will use partial reachable set"
            )

        walker._prune_session()
        await session.save()
        return walker.reachable
