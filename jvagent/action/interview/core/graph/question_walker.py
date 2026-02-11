"""QuestionWalker for traversing QuestionNodes using jvspatial walker-node pattern.

This module provides QuestionWalker, a specialized walker that:
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
from .state_node import StateNode
from ..foundation.enums import ValidationStatus, InterviewState


logger = logging.getLogger(__name__)


class QuestionWalker(Walker):
    """Walker that traverses QuestionNodes in a tree-based interview flow.

    Implements jvspatial's walker-node pattern for efficient graph traversal using
    @on_visit decorators and automatic queue management. The walker's presence on
    a node triggers operations - the question graph guides the walk path.

    Architecture (Walker-Node Pattern):
    ===================================
    Instead of manual traversal, QuestionWalker uses @on_visit decorators:
    - @on_visit(QuestionNode): Checks if answered, queues prompt or validates
    - @on_visit(StateNode): Records terminal state and executes transition

    The Walker.spawn() starts traversal, run() processes the queue, and
    @on_visit hooks are automatically dispatched for each visited node.

    Usage:
    ======
    walker = QuestionWalker(
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

    # Pydantic fields with proper defaults (using Field for mutable types)
    interview_session: Optional[Any] = None  # InterviewSession
    interaction: Optional[Any] = None  # Interaction
    interview_action: Optional[Any] = None  # InterviewInteractAction
    interact_visitor: Optional[Any] = None  # InteractWalker (parent walker for branch evaluation)
    current_intent: Optional[Any] = None  # Intent for this turn (e.g. Intent.DECLINE for required-field decline detection)

    # Mutable defaults must use Field/PrivateAttr(default_factory=...) to avoid sharing
    directives: List[str] = Field(default_factory=list)
    _visited_nodes: Set[str] = PrivateAttr(default_factory=set)

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

    async def _get_node_by_id(self, node_id: str) -> Optional[Union[QuestionNode, StateNode, Any]]:
        """Fetch node from graph by ID.

        Args:
            node_id: The node ID to look up

        Returns:
            Node instance (QuestionNode, StateNode, or InterviewInteractAction) or None
        """
        from jvspatial.core import Node
        try:
            return await Node.get(node_id)
        except Exception as e:
            logger.warning(f"Failed to get node by ID '{node_id}': {e}")
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
        if directive:
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
        their input.

        Args:
            here: The QuestionNode being visited
            question_name: Name of the question field
            feedback: Error message from the validator
        """
        error_msg = feedback or f"Please provide a valid value for {question_name}."
        self.directives.append(error_msg)
        self.current_question_name = question_name
        self.current_question = here
        self.interview_session.target_node = here.id
        await self.interview_session.save()

    async def _handle_valid_response(
        self, here: QuestionNode, question_name: str, final_value: Any
    ) -> None:
        """Update session with processed value.

        After successful validation, this method updates the session response
        if the value was modified by processing or correction.

        Args:
            here: The QuestionNode being visited
            question_name: Name of the question field
            final_value: The validated/corrected value
        """
        # Update with processed/corrected value if changed
        response_value = self.interview_session.responses[question_name]
        if final_value != response_value:
            self.interview_session.set_response(question_name, final_value)

    async def _continue_traversal(self, here: QuestionNode, question_name: str) -> None:
        """Evaluate outgoing edges and visit the first that returns a target node.

        All edges are evaluated in order (by branch_index).
        The first edge whose evaluate() returns a non-None node is used; the
        walker visits that target node. Branch path is recorded inside the edge's evaluate().

        Args:
            here: The QuestionNode being visited
            question_name: Name of the question field (used as implicit question for conditions)
        """
        edges = await here.edges(direction="out")
        # Evaluate conditional edges before default: use (has_condition, branch_index)
        # so edges with a condition run first (and in branch order), then default edges.
        ordered = sorted(
            edges,
            key=lambda e: (
                0 if e.condition else 1,
                e.branch_index if e.branch_index is not None else 999,
            ),
        )
        for edge in ordered:
            target = await edge.evaluate(
                self.interview_session,
                question_name,
                self.interact_visitor,
            )
            if target is not None:
                await self.visit(target)
                return

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

        logger.warning(f"On question node visit: node id={here.label}")
        
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
        is_valid, final_value, feedback = await self._process_and_validate_response(
            here, question_name
        )

        if not is_valid:
            # Invalid input - queue error directive and disengage
            await self._handle_invalid_response(here, question_name, feedback)
            return

        # Valid input - update session and continue walking
        await self._handle_valid_response(here, question_name, final_value)
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
        logger.warning(f"On state node visit: node id={here.label}")

        if not self.interview_session:
            return

        # Record terminal state
        self.terminal_state = here.state_type
        self.terminal_state_node = here

        # Execute state node - handles transition and returns directive
        directive = await here.execute(self)
        if directive:
            self.directives.append(directive)

        # Update position and return
        await self._update_target_node(here)