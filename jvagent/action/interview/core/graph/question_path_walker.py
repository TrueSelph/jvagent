"""QuestionPathWalker for determining next target and reachable questions.

This module provides QuestionPathWalker, a lightweight walker that traverses
the question graph following the active branch path (using BranchCache) to:
- Find the next target (QuestionNode or StateNode) on the active path
- Collect all reachable questions on the active path
- Sync session state after updates (invalidate cache, traverse, prune, save)
- Determine if all reachable questions are answered (→ REVIEW)

find_next_target may return a QuestionNode (unanswered) or a StateNode (e.g., REVIEW
when all questions are answered). StateNodes are also traversed to discover
downstream QuestionNodes (e.g., edit flow from REVIEW).

Modes:
- find_next: Uses existing cache, stops at first unanswered or returns StateNode
- collect_all: Uses existing cache, traverses full path (read-only)
- sync_post_update: Invalidates cache, traverses full path, prunes unreachable data
"""

import logging
from typing import Any, Optional, Set, Union

from pydantic import PrivateAttr
from jvspatial.core import Walker, on_visit

from .question_node import QuestionNode
from .state_node import StateNode
from ..utils.cache_utils import BranchCache

logger = logging.getLogger(__name__)


class QuestionPathWalker(Walker):
    """Lightweight walker for determining next target node and reachable questions.
    
    This walker traverses the question graph following the active branch path
    (using BranchCache) to:
    - Find the next target (QuestionNode or StateNode) on the active path
    - Collect all reachable questions on the active path
    - Sync session state after updates (invalidate cache, prune unreachable data)
    - Determine if all reachable questions are answered (→ REVIEW)
    
    find_next_target returns a QuestionNode when an unanswered question is found,
    or a StateNode (e.g., REVIEW) when all questions are answered. StateNodes
    are traversed to discover downstream QuestionNodes (e.g., edit flow).
    
    Modes:
    - find_next: Uses existing cache, stops at first unanswered or StateNode
    - collect_all: Uses existing cache, traverses full path (read-only)
    - sync_post_update: Invalidates cache, traverses full path, prunes session data
    
    Usage:
        # Find next target (QuestionNode or StateNode)
        next_node = await QuestionPathWalker.find_next_target(session, first_node, visitor)
        
        # Get all reachable questions
        reachable = await QuestionPathWalker.get_reachable_questions(session, first_node, visitor)
        
        # Post-update sync (prune unreachable responses)
        reachable = await QuestionPathWalker.sync(session, first_node, visitor, self)
    """

    interview_session: Optional[Any] = None
    interact_visitor: Optional[Any] = None
    interview_action: Optional[Any] = None
    
    # Mode: "find_next" stops at first unanswered, "collect_all" traverses entire path,
    # "sync_post_update" invalidates cache, traverses full path, prunes session
    _mode: str = PrivateAttr(default="find_next")
    _next_target: Optional[Any] = PrivateAttr(default=None)
    _reachable: Set[str] = PrivateAttr(default_factory=set)
    _visited_ids: Set[str] = PrivateAttr(default_factory=set)

    @on_visit(QuestionNode, StateNode)
    async def on_path_node(self, here: Union[QuestionNode, StateNode]) -> None:
        """Handle QuestionNode or StateNode: collect reachable, set next target, follow edges."""
        if here.id in self._visited_ids:
            return
        self._visited_ids.add(here.id)

        implicit_name = (
            here.state.get("name", here.label)
            if hasattr(here, "state") and isinstance(getattr(here, "state"), dict)
            else here.label
        )

        if isinstance(here, QuestionNode):
            self._reachable.add(implicit_name)
            if implicit_name not in self.interview_session.responses:
                if self._mode == "find_next" and self._next_target is None:
                    self._next_target = here
                    return

        if isinstance(here, StateNode):
            if self._mode == "find_next" and self._next_target is None:
                self._next_target = here

        from .question_edge import QuestionEdge
        edges = await here.edges(direction="out")
        if not edges:
            return

        # Check if this UNANSWERED node has conditional branches and no cache to guide traversal
        if isinstance(here, QuestionNode) and self._mode in ("collect_all", "sync_post_update"):
            # Only check branches if the question is UNANSWERED
            if implicit_name not in self.interview_session.responses:
                has_conditional_branches = any(
                    hasattr(e, 'condition') and e.condition is not None 
                    for e in edges
                )
                
                if has_conditional_branches:
                    # Check if BranchCache has a decision for this question
                    branch_cache = BranchCache(self.interview_session)
                    cached_target = branch_cache.get(implicit_name)
                    
                    if cached_target is None:
                        # No cache guidance - cannot determine which branch to take
                        logger.debug(
                            f"QuestionPathWalker: stopping at unanswered '{implicit_name}' - "
                            f"has {len(edges)} conditional branch(es) but no cache to guide traversal"
                        )
                        return  # Stop at undetermined branch point

        ordered = QuestionEdge.sort_by_priority(edges)
        for edge in ordered:
            target = await edge.evaluate(
                self.interview_session,
                implicit_name,
                self.interact_visitor,
                self.interview_action,
                edge_count=len(edges),
            )
            if target is not None:
                await self.visit(target)
                return

    @property
    def next_target(self) -> Optional[Any]:
        """Get the next target node (QuestionNode or StateNode) found during traversal."""
        return self._next_target

    @property
    def reachable(self) -> Set[str]:
        """Get the set of question names reachable on the active branch path."""
        return self._reachable

    def _prune_session(self) -> None:
        """Prune responses, validation_results, and update_queue for unreachable questions.

        Safety: if the reachable set is empty (e.g. traversal failed silently),
        pruning is skipped to prevent catastrophic data loss.
        """
        session = self.interview_session
        if not session:
            return

        if not self._reachable:
            logger.warning(
                "QuestionPathWalker._prune_session: reachable set is empty — "
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
                logger.debug(f"QuestionPathWalker: pruned unreachable response '{field}'")

        if session.update_queue:
            session.update_queue = [
                e for e in session.update_queue if e["field"] in self._reachable
            ]

        if pruned_questions:
            logger.info(
                f"QuestionPathWalker: pruned {len(pruned_questions)} unreachable "
                f"response(s): {pruned_questions}"
            )

    @classmethod
    async def sync(
        cls,
        session: Any,
        first_node: Optional[Any] = None,
        interact_visitor: Optional[Any] = None,
        interview_action: Optional[Any] = None,
        invalidate_cache: bool = True,
    ) -> Set[str]:
        """Run post-update sync: walk the graph, prune stale data, return reachable set.

        When invalidate_cache is True, invalidates the BranchCache so every conditional
        edge is evaluated fresh. When False (e.g. SUBMISSION or REVIEW), preserves
        cache populated during resolution and walker spawn.

        Args:
            session: InterviewSession with responses to prune.
            first_node: The first QuestionNode in the graph (entry point).
            interact_visitor: Optional InteractWalker for branch function evaluation.
            interview_action: Optional InterviewInteractAction for branch evaluation.
            invalidate_cache: If True, clear branch cache before traversal (UPDATE intent).
                If False, preserve cache (SUBMISSION, REVIEW).

        Returns:
            Set of reachable question names on the active path.
        """
        if first_node is None:
            logger.warning("QuestionPathWalker.sync: first_node is None, skipping traversal")
            return set()

        branch_cache = BranchCache(session)
        if invalidate_cache:
            branch_cache.invalidate_all()
            branch_cache.clear_pruned_responses()

        walker = cls(
            interview_session=session,
            interact_visitor=interact_visitor,
            interview_action=interview_action,
        )
        walker._mode = "sync_post_update"

        try:
            await walker.spawn(first_node)
        except Exception:
            logger.exception(
                "QuestionPathWalker.sync: error during traversal — "
                "pruning will use partial reachable set"
            )

        walker._prune_session()
        await session.save()
        return walker.reachable

    @classmethod
    async def find_next_target(
        cls,
        session: Any,
        first_node: Optional[QuestionNode] = None,
        visitor: Optional[Any] = None,
        interview_action: Optional[Any] = None
    ) -> Optional[Any]:
        """Find the next target on the active branch path.
        
        Traverses the question graph following conditional branches (using BranchCache)
        and stops at the first unanswered question, or returns a StateNode (e.g., REVIEW)
        when all questions are answered.
        
        Args:
            session: InterviewSession with responses and branch cache
            first_node: The first QuestionNode in the graph (entry point)
            visitor: Optional InteractWalker for branch function evaluation
        
        Returns:
            Next QuestionNode (unanswered) or StateNode (e.g., REVIEW) on the path,
            or None if traversal fails
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
