"""InterviewWalker for traversing QuestionNodes using jvspatial walker-node pattern.

This module provides InterviewWalker, a specialized walker that:
- Uses @on_visit decorators for automatic node dispatch (walker-node pattern)
- Traverses QuestionNodes based on conditional branch conditions
- Records branch paths for path-change detection and pruning
- Handles state transitions via StateNode visits
- Returns directives to InterviewInteractAction

The walker-node pattern replaces manual traversal with declarative node handling.
When spawned on a target node, the walker's presence triggers operations via
@on_visit decorators. The question graph guides the walk path - no explicit
"find_next_question" logic is needed.

Key Components:
- @on_visit(QuestionNode): Handle question node visits (prompt or validate)
- @on_visit(StateNode): Handle state node visits (state transitions)
"""

import logging
from typing import Any, ClassVar, Dict, List, Optional, Set, Tuple, Union

from pydantic import Field, PrivateAttr
from jvspatial.core import Walker, on_visit

from .question_node import QuestionNode
from .question_path_walker import QuestionPathWalker
from .state_node import StateNode
from ..foundation.enums import ValidationStatus, InterviewState, Intent
from ..utils.handler_utils import invoke_async_with_optional_context

logger = logging.getLogger(__name__)


class InterviewWalker(Walker):
    """Walker that traverses QuestionNodes in a tree-based interview flow.

    Implements jvspatial's walker-node pattern for efficient graph traversal using
    @on_visit decorators and automatic queue management. The walker's presence on
    a node triggers operations - the question graph guides the walk path.

    Architecture (Walker-Node Pattern):
    ===================================
    Instead of manual traversal, InterviewWalker uses @on_visit decorators:
    - @on_visit(QuestionNode): Checks if answered, queues prompt or validates
    - @on_visit(StateNode): Records terminal state and executes transition

    The Walker.spawn() starts traversal, run() processes the queue, and
    @on_visit hooks are automatically dispatched for each visited node.

    Usage:
    ======
    walker = InterviewWalker(
        interview_session=session,
        interaction=interaction,
        interact_visitor=interact_walker,
        interview_action=self
    )
    await walker.spawn(target_node)
    # Directives are now in walker.directives

    Attributes (Walker State):
        interview_session: InterviewSession - Current interview session
        interaction: Interaction - Current interaction (optional)
        interact_visitor: InteractWalker - Parent walker for branch evaluation
        interview_action: InterviewInteractAction - For directive building
        directives: List[str] - Collected directives to return to caller
        terminal_state: Optional[InterviewState] - State reached if terminal
    """

    # Pydantic fields — use Optional[Any] because these types are from other
    # packages and cannot be imported directly without creating circular imports.
    # jvspatial Walker is a Pydantic BaseModel, so TYPE_CHECKING forward refs
    # cannot be used here (Pydantic resolves annotations at runtime).
    interview_session: Optional[Any] = None  # InterviewSession
    interaction: Optional[Any] = None  # Interaction
    interview_action: Optional[Any] = None  # InterviewInteractAction
    interact_visitor: Optional[Any] = None  # InteractWalker
    current_intent: Optional[Intent] = None  # Intent enum (safe: imported directly)

    # Mutable defaults must use Field/PrivateAttr(default_factory=...) to avoid sharing
    directives: List[str] = Field(default_factory=list)
    _visited_nodes: Set[str] = PrivateAttr(default_factory=set)
    # ("replace"|"append", directive) from @input_directive_override for the previous question
    _pending_directive_override: Optional[Tuple[str, str]] = PrivateAttr(default=None)

    # Terminal state tracking
    terminal_state: Optional[InterviewState] = None
    terminal_state_node: Optional[Any] = None  # StateNode

    # State target mapping (class-level constant)
    STATE_TARGETS: ClassVar[Dict[str, InterviewState]] = {
        "REVIEW": InterviewState.REVIEW,
        "COMPLETED": InterviewState.COMPLETED,
        "CANCELLED": InterviewState.CANCELLED,
    }
    
    def _get_state_from_target(self, target: str) -> Optional[InterviewState]:
        """Get InterviewState from state target name.

        Args:
            target: State target name (e.g., "REVIEW")

        Returns:
            InterviewState if target is a valid state, None otherwise
        """
        return self.STATE_TARGETS.get(target)

    def _is_state_target(self, target: str) -> bool:
        """Check whether *target* refers to a state node (REVIEW, COMPLETED, CANCELLED).

        Args:
            target: Target name string to check.

        Returns:
            True if the name maps to a known InterviewState, False otherwise.
        """
        return target in self.STATE_TARGETS

    async def get_reachable_required_questions(self, session: Any) -> Set[str]:
        """Compute the set of required questions reachable on the current active path.

        Delegates to QuestionPathWalker.sync for a cache-independent graph traversal,
        then intersects the reachable set with the session's required questions.

        Args:
            session: InterviewSession instance.

        Returns:
            Set of required question names reachable on the active branch path.
        """
        # Resolve first node from session.question_graph
        first_node = await self._resolve_first_node(session)
        reachable = await QuestionPathWalker.sync(
            session, first_node, self.interact_visitor, self.interview_action
        )
        required = set(session.get_required_questions())
        return reachable & required

    async def _resolve_first_node(self, session: Any) -> Optional[QuestionNode]:
        """Resolve the first QuestionNode from the session's question_graph.

        Uses Node.get to look up the first question by name from the
        QuestionNodeCache, falling back to a graph query when necessary.

        Args:
            session: InterviewSession with question_graph populated.

        Returns:
            First QuestionNode or None.
        """
        from ..utils.cache_utils import QuestionNodeCache

        if not session.question_graph:
            return None
        first_name = session.question_graph[0].get("name")
        if not first_name:
            return None
        cache = QuestionNodeCache(session)
        node = await cache.get_cached_node_by_id(first_name)
        if node:
            return node
        # Fallback: query graph for the node by label
        from jvspatial.core import Node as SpatialNode
        try:
            results = await SpatialNode.find(label=first_name)
            if results:
                return results[0]
        except Exception:
            pass
        return None

    # =========================================================================
    # Helper Methods for on_question_node() - Extracted for clarity
    # =========================================================================

    async def _handle_unanswered_question(
        self, here: QuestionNode, question_name: str
    ) -> bool:
        """Handle case where no response exists - delegate to QuestionNode.

        When the walker encounters a question without an extracted value in the
        session, this method delegates to the QuestionNode to determine the
        appropriate action. The QuestionNode may:
        - Return a directive (question prompt, required-field decline message)
        - Handle the response internally (e.g., set N/A for optional decline)
          and return None to signal the walker should continue traversal.

        Args:
            here: The QuestionNode being visited
            question_name: Name of the question field

        Returns:
            True if the walker should continue traversal (response handled, no directive),
            False if the walker should stop (directive queued or awaiting input)
        """
        directive = await here.execute(self)
        if directive or self._pending_directive_override:
            # Apply @input_directive_override from previous question if any
            if self._pending_directive_override:
                mode, override_directive = self._pending_directive_override
                self._pending_directive_override = None
                if mode == "replace":
                    self.directives.append(override_directive)
                else:  # append
                    if directive:
                        self.directives.append(directive)
                    self.directives.append(override_directive)
            elif directive:
                self.directives.append(directive)
            self.interview_session.target_node = here.id
            await self.interview_session.save()
            return False

        # No directive returned. Check if QuestionNode handled the response
        # (e.g., optional DECLINE sets N/A without returning a directive)
        if question_name in self.interview_session.responses:
            return True

        # No directive and no response — stay on this question
        self.interview_session.target_node = here.id
        await self.interview_session.save()
        return False

    async def _process_and_validate_response(
        self, here: QuestionNode, question_name: str
    ) -> Tuple[bool, Any, Optional[str]]:
        """Execute handlers and validators on the extracted response.

        Runs the full validation pipeline:
        1. Execute @input_handler (pre-processing/transformation)
        2. Execute @input_validator (validation with feedback)

        Args:
            here: The QuestionNode being visited
            question_name: Name of the question field

        Returns:
            Tuple of (is_valid, final_value, feedback):
            - is_valid: True if validation passed
            - final_value: The processed/corrected value
            - feedback: Error message if validation failed, None otherwise
        """
        response_value = self.interview_session.responses[question_name]

        # Execute @input_handler if registered (pre-processing)
        processed_value = await here.process_input(
            response_value,
            self.interview_session,
            self.interaction,
            visitor=self.interact_visitor,
            interview_action=self.interview_action,
        )

        # Execute @input_validator if registered (validation)
        validation_status, feedback, corrected_value = await here.validate_response(
            processed_value,
            self.interview_session,
            visitor=self.interact_visitor,
            interview_action=self.interview_action,
        )
        final_value = corrected_value if corrected_value is not None else processed_value

        is_valid = validation_status == ValidationStatus.VALID
        return is_valid, final_value, feedback

    async def _handle_invalid_response(
        self, here: QuestionNode, question_name: str, feedback: Optional[str]
    ) -> None:
        """Queue validator's error directive and disengage.

        When validation fails, this method queues the error directive and updates
        the session to stay on the current question, allowing the user to correct
        their input. If this was a pending update, restores the old value.

        Args:
            here: The QuestionNode being visited
            question_name: Name of the question field
            feedback: Error message from the validator
        """
        error_msg = feedback or f"Please provide a valid value for {question_name}."
        self.directives.append(error_msg)
        self.interview_session.target_node = here.id

        # Restore old value if this was a pending update
        queue = self.interview_session.update_queue
        entry = next((e for e in queue if e["field"] == question_name), None)
        if entry and entry.get("old_value") is not None:
            self.interview_session.set_response(question_name, entry["old_value"])

        await self.interview_session.save()

    async def _handle_valid_response(
        self, here: QuestionNode, question_name: str, final_value: Any
    ) -> None:
        """Update session with processed value and pop from update queue.

        After successful validation, this method updates the session response
        if the value was modified by processing or correction. If this question
        had a pending update, it is removed from the queue.

        Args:
            here: The QuestionNode being visited
            question_name: Name of the question field
            final_value: The validated/corrected value
        """
        # Update with processed/corrected value if changed
        response_value = self.interview_session.responses[question_name]
        if final_value != response_value:
            self.interview_session.set_response(question_name, final_value)

        # Pop from update_queue if this was a pending update
        self.interview_session.pop_update(question_name)

    async def _apply_directive_override(
        self, question_name: str, final_value: Any
    ) -> None:
        """Invoke @input_directive_override for the field just stored and cache result.

        If the override returns a directive, stores it in _pending_directive_override
        for use when the next node would queue a directive. Supports:
        - None: use default (no override)
        - str: replace next directive with this string
        - Tuple[str, str]: ("append"|"replace", directive)

        Args:
            question_name: Name of the field that was just validated and stored
            final_value: The value that was stored
        """
        if not self.interview_action:
            return
        override_func = self.interview_action.get_input_directive_override(question_name)
        if not override_func or not callable(override_func):
            return
        try:
            result = await invoke_async_with_optional_context(
                override_func,
                question_name,
                final_value,
                self.interview_session,
                self.interaction,
                visitor=self.interact_visitor,
                interview_action=self.interview_action,
            )
        except Exception as e:
            logger.warning(
                f"input_directive_override for '{question_name}' raised: {e}",
                exc_info=True,
            )
            return
        if result is None:
            return
        if isinstance(result, str):
            self._pending_directive_override = ("replace", result)
        elif isinstance(result, tuple) and len(result) == 2:
            mode, directive = result
            if mode in ("append", "replace") and directive:
                self._pending_directive_override = (mode, directive)

    async def _continue_traversal(self, here: QuestionNode, question_name: str) -> None:
        """Evaluate outgoing edges and visit the first that returns a target node.

        All edges are evaluated in order (by branch_index).
        The first edge whose evaluate() returns a non-None node is used; the
        walker visits that target node. Branch path is recorded inside the edge's evaluate().

        Args:
            here: The QuestionNode being visited
            question_name: Name of the question field (used as implicit question for conditions)
        """
        from .question_edge import QuestionEdge
        
        edges = await here.edges(direction="out")
        ordered = QuestionEdge.sort_by_priority(edges)
        for edge in ordered:
            target = await edge.evaluate(
                self.interview_session,
                question_name,
                self.interact_visitor,
                self.interview_action,
                edge_count=len(edges),
            )
            if target is not None:
                await self.visit(target)
                return

    async def _continue_traversal_from_state(self, here: StateNode) -> bool:
        """Evaluate outgoing edges from a StateNode and visit the first valid target.

        Used when a StateNode returns no directive (e.g., REVIEW with auto_confirm).
        Allows the walker to traverse from StateNode to StateNode (e.g., REVIEW -> COMPLETED).

        Args:
            here: The StateNode being visited

        Returns:
            True if traversal continued to a target node, False if no edge found
        """
        from .question_edge import QuestionEdge
        
        edges = await here.edges(direction="out")
        if not edges:
            logger.warning(
                f"StateNode {here.label} returned no directive but has no outgoing edges. "
                f"Session will remain on {here.label}."
            )
            return False
        
        ordered = QuestionEdge.sort_by_priority(edges)
        for edge in ordered:
            # For StateNode edges, use the node's label as implicit_question
            target = await edge.evaluate(
                self.interview_session,
                here.label,
                self.interact_visitor,
                self.interview_action,
                edge_count=len(edges),
            )
            if target is not None:
                await self.visit(target)
                return True
        
        logger.warning(
            f"StateNode {here.label} has outgoing edges but none evaluated to a valid target. "
            f"Session will remain on {here.label}."
        )
        return False

    async def _update_target_node(self, here: Union[QuestionNode, StateNode]) -> None:
        """Update session's target_node and save.

        Updates the session's position tracking to the current node
        and persists the change.

        Args:
            here: The node to set as the target
        """
        self.interview_session.target_node = here.id
        await self.interview_session.save()

    @on_visit(QuestionNode)
    async def on_question_node(self, here: QuestionNode) -> None:
        """Handle visiting a QuestionNode during graph traversal.

        This @on_visit decorator is called automatically by jvspatial's Walker
        when a QuestionNode is visited during spawn() traversal.

        Traversal Logic:
        ================
        1. Check for infinite loops (visited node tracking)
        2. If no extracted value exists → delegate to QuestionNode
           - If directive returned → queue and stop (e.g., question prompt, decline rejection)
           - If response handled without directive (e.g., optional DECLINE) → continue walking
        3. If extracted value exists → execute handlers and validators
           - If invalid → queue error directive and return (disengage)
           - If valid → update session, continue walking

        Args:
            here: QuestionNode being visited
        """

        
        if not self.interview_session:
            return

        question_name = here.state.get("name", here.label) if hasattr(here, "state") else here.label

        # Prevent infinite loops
        if here.id in self._visited_nodes:
            return
        self._visited_nodes.add(here.id)

        # Branch 1: No extracted value - delegate to QuestionNode for inference
        if question_name not in self.interview_session.responses:
            should_continue = await self._handle_unanswered_question(here, question_name)
            if not should_continue:
                return
            # QuestionNode handled the response without a directive (e.g. optional DECLINE)
            # Clear the consumed intent to prevent it from affecting subsequent questions
            self.current_intent = None
            await self._continue_traversal(here, question_name)
            return

        # Branch 2: Extracted value exists - process and validate
        # Fast-path: skip re-validation for answered questions not in update queue
        if not self.interview_session.has_pending_update(question_name):
            await self._continue_traversal(here, question_name)
            return

        is_valid, final_value, feedback = await self._process_and_validate_response(
            here, question_name
        )

        if not is_valid:
            # Invalid input - queue error directive and disengage
            await self._handle_invalid_response(here, question_name, feedback)
            return

        # Valid input - update session, apply directive override if any, continue walking
        await self._handle_valid_response(here, question_name, final_value)
        await self._apply_directive_override(question_name, final_value)
        await self._continue_traversal(here, question_name)

    @on_visit(StateNode)
    async def on_state_node(self, here: StateNode) -> None:
        """Handle visiting a StateNode during graph traversal.

        This @on_visit decorator is called automatically by jvspatial's Walker
        when a StateNode is visited during spawn() traversal.

        Follows the same pattern as on_question_node:
        1. Record terminal state for caller reference
        2. Execute state node (handles transition + directive generation)
           - For REVIEW: returns confirmation directive
           - For COMPLETED: triggers completion handler, queues to visitor
           - For CANCELLED: handles cancellation, queues to visitor
        3. Update position and return

        Args:
            here: StateNode being visited
        """

        if not self.interview_session:
            return

        # Record terminal state
        self.terminal_state = here.state_type
        self.terminal_state_node = here

        # Before REVIEW: prune unreachable responses so review directive lists correct data
        if here.state_type == InterviewState.REVIEW and self.interview_action:
            first_node = await self.interview_action._get_first_question_node(
                self.interview_session
            )
            if first_node:
                await QuestionPathWalker.sync(
                    self.interview_session, first_node, self.interact_visitor, self.interview_action,
                    invalidate_cache=False
                )

        # Execute state node - handles transition and returns directive
        directive = await here.execute(self)
        directive_queued = False
        if directive or self._pending_directive_override:
            if self._pending_directive_override:
                mode, override_directive = self._pending_directive_override
                self._pending_directive_override = None
                if mode == "replace":
                    self.directives.append(override_directive)
                else:  # append
                    if directive:
                        self.directives.append(directive)
                    self.directives.append(override_directive)
            elif directive:
                self.directives.append(directive)
            directive_queued = True

        # Update position — skip for terminal states (COMPLETED/CANCELLED) where
        # the session has been removed by cleanup. Calling save() on a deleted
        # session would re-persist it to the database.
        if not here.is_terminal():
            if not directive_queued:
                # No directive (e.g., auto_confirm) — follow outgoing edges
                continued = await self._continue_traversal_from_state(here)
                if not continued:
                    # No edge found, fall back to updating target_node
                    await self._update_target_node(here)
            else:
                await self._update_target_node(here)