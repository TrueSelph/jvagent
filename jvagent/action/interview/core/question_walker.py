"""QuestionWalker for traversing QuestionNodes in tree-based interview flows.

This module provides QuestionWalker, a specialized walker that traverses
QuestionNodes based on conditional edges, triggers validations/handlers,
and returns directives to InterviewStateInteractAction.
"""

import logging
from typing import TYPE_CHECKING, Any, ClassVar, Dict, List, Optional, Tuple, Union

from jvspatial.core import Walker
from jvagent.memory import Interaction

from .question_branch_evaluator import QuestionBranchEvaluator
from .interview_session import InterviewSession
from .question_node import QuestionNode
from .state_node import StateNode
from .enums import ValidationStatus, InterviewState

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)


class QuestionWalker(Walker):
    """Walker that traverses QuestionNodes in a tree-based interview flow.

    QuestionWalker is responsible for:
    - Finding the next unanswered question based on tree traversal and conditions
    - Processing input via QuestionNode input handlers
    - Validating responses via QuestionNode validators
    - Getting directives from QuestionNodes
    - Respecting conditional branching based on previous answers

    Attributes:
        interview_session: InterviewSession - Current interview session
        interaction: Interaction - Current interaction
        current_question: Optional[QuestionNode] - Currently active question
    """

    interview_session: Optional[InterviewSession] = None
    interaction: Optional[Interaction] = None
    current_question: Optional[QuestionNode] = None
    question_directive_template: Optional[str] = None
    
    # State target mapping
    STATE_TARGETS: ClassVar[Dict[str, InterviewState]] = {
        "REVIEW": InterviewState.REVIEW,
        "COMPLETED": InterviewState.COMPLETED,
        "CANCELLED": InterviewState.CANCELLED,
    }
    
    def _is_state_target(self, target: str) -> bool:
        """Check if a target is a named state target.
        
        Args:
            target: Target string to check
            
        Returns:
            True if target is a state name, False otherwise
        """
        return target in self.STATE_TARGETS
    
    def _get_state_from_target(self, target: str) -> Optional[InterviewState]:
        """Get InterviewState from state target name.
        
        Args:
            target: State target name (e.g., "REVIEW")
            
        Returns:
            InterviewState if target is a valid state, None otherwise
        """
        return self.STATE_TARGETS.get(target)
    
    async def _handle_state_target(
        self,
        target: str,
        session: InterviewSession,
        interview_action: Optional[Any] = None
    ) -> bool:
        """Handle a state target (either string or StateNode).
        
        If target is a StateNode, executes it to trigger state transition.
        If target is a string, finds and executes the corresponding StateNode.
        
        Args:
            target: State target name (e.g., "REVIEW") or StateNode
            session: Interview session
            interview_action: Optional InterviewInteractAction to search StateNodes from
            
        Returns:
            True if state was handled, False otherwise
        """
        # Check if it's a string state target
        if isinstance(target, str) and self._is_state_target(target):
            # Try to find StateNode for this target
            if interview_action:
                state_nodes = await interview_action.nodes(direction="out", node=StateNode)
                target_upper = target.upper()
                for state_node in state_nodes:
                    logger.debug(
                        f"Checking StateNode: label={state_node.label}, target={target}, target.upper()={target_upper}"
                    )
                    # Compare labels (both should be uppercase)
                    if state_node.label.upper() == target_upper:
                        logger.info(
                            f"Found StateNode for target '{target}', executing state transition to {state_node.state_type.value}"
                        )
                        await state_node.execute(session, self)
                        # StateNode.execute() already saved the session
                        return True
                
                logger.warning(
                    f"StateNode not found for target '{target}' (checked {len(state_nodes)} state nodes). "
                    f"Available labels: {[sn.label for sn in state_nodes]}"
                )
            
            # Fallback: transition state directly if StateNode not found
            # Get the InterviewState from the target string
            target_state = self._get_state_from_target(target)
            if target_state:
                # Transition state directly
                session.transition_to(target_state)
                await session.save()
                return True
            else:
                # Invalid state target
                logger.warning(
                    f"Invalid state target '{target}' - not a recognized state."
                )
                return False
        
        # Check if target is a StateNode instance
        if isinstance(target, StateNode):
            await target.execute(session, self)
            # StateNode.execute() already saved the session
            return True
        
        return False

    async def find_next_question(
        self,
        session: InterviewSession,
        interview_action: Optional[Any] = None,
        start_from: Optional[str] = None
    ) -> Optional[Union[QuestionNode, StateNode]]:
        """Find next node (question or state) based on tree traversal.

        Traverses the question tree starting from session.active_question_key (if set),
        or from the first unanswered question, respecting conditional branches.
        When no unanswered questions exist, follows edges to StateNodes (e.g., REVIEW).
        Updates session.active_question_key with the found question (if QuestionNode).

        Args:
            session: Interview session (contains active_question_key for position tracking)
            interview_action: Optional InterviewStateInteractAction to search question nodes from
            start_from: Optional question name to start traversal from (overrides session.active_question_key)

        Returns:
            Next QuestionNode to ask, StateNode to transition to, or None if graph is incomplete
        """
        unanswered = session.get_unanswered_questions()
        
        # If there are unanswered questions, find the next one
        if unanswered:
            # Determine starting point: start_from > session.active_question_key > first unanswered
            if start_from:
                current_question_name = start_from
            elif session.active_question_key and session.active_question_key in unanswered:
                # Continue from where we left off
                current_question_name = session.active_question_key
            else:
                # Find first unanswered question by traversing tree from root
                current_question_name = await self._find_first_unanswered_in_tree(session, interview_action)
                if not current_question_name:
                    # No unanswered questions found in traversal, check for StateNode edges
                    return await self._find_terminal_state_node(session, interview_action)

            # Get the question node by name
            question_node = await self._get_question_node_by_name(
                current_question_name,
                session,
                interview_action
            )
            if question_node:
                # Update session with current question position
                session.active_question_key = current_question_name
                await session.save()
                # Cache in walker for this request only (transient)
                self.current_question = question_node
                return question_node

        # No unanswered questions - check for StateNode edges from current or last question
        return await self._find_terminal_state_node(session, interview_action)

    async def _find_terminal_state_node(
        self,
        session: InterviewSession,
        interview_action: Optional[Any] = None
    ) -> Optional[StateNode]:
        """Find StateNode edge from current or last answered question.
        
        When all questions are answered, this method traverses the graph from the
        last answered question to find StateNode edges (typically REVIEW).
        
        Args:
            session: Interview session
            interview_action: Optional InterviewInteractAction to search nodes from
            
        Returns:
            StateNode if found, None otherwise
        """
        if not interview_action:
            return None
        
        # Get the last answered question or current active question
        answered_questions = session.get_answered_questions()
        if not answered_questions:
            # No questions answered yet - shouldn't reach here, but handle gracefully
            return None
        
        # Start from the last answered question (most recent)
        last_question_name = answered_questions[-1] if answered_questions else session.active_question_key
        
        # Also check active_question_key if it exists and is answered
        if session.active_question_key and session.active_question_key in answered_questions:
            last_question_name = session.active_question_key
        
        if not last_question_name:
            return None
        
        # Get the question node
        question_node = await self._get_question_node_by_name(
            last_question_name,
            session,
            interview_action
        )
        
        if not question_node:
            return None
        
        # Check for outgoing StateNode edges
        state_nodes = await question_node.nodes(direction="out", node=StateNode)
        
        # Prefer REVIEW state node if multiple exist
        for state_node in state_nodes:
            if state_node.state_type == InterviewState.REVIEW:
                logger.debug(
                    f"Found REVIEW StateNode edge from terminal question '{last_question_name}'"
                )
                return state_node
        
        # Return first StateNode found if no REVIEW
        if state_nodes:
            logger.debug(
                f"Found StateNode '{state_nodes[0].label}' edge from terminal question '{last_question_name}'"
            )
            return state_nodes[0]
        
        # No StateNode edge found - graph may be incomplete
        logger.warning(
            f"No StateNode edge found from terminal question '{last_question_name}'. "
            f"Graph may be incomplete."
        )
        return None

    async def _find_first_unanswered_in_tree(
        self,
        session: InterviewSession,
        interview_action: Optional[Any] = None
    ) -> Optional[str]:
        """Find first unanswered question by traversing tree from root.

        If session.active_question_key is set (even if answered), continues traversal
        from that position. Otherwise starts from the first question in question_index.

        Args:
            session: Interview session (may contain active_question_key for position)
            interview_action: Optional InterviewInteractAction to get QuestionNodes from
            

        Returns:
            Name of first unanswered question, or None if all answered or state target encountered
        """
        # Start from first question in question_index
        if not session.question_index:
            return None
        
        # Build a map of question names to configs
        question_map = {q.get("name"): q for q in session.question_index if q.get("name")}

        # Determine starting point
        unanswered = set(session.get_unanswered_questions())
        start_question = None
        skip_until = None

        if session.active_question_key:
            if session.active_question_key in unanswered:
                # Continue from unanswered active question
                start_question = session.active_question_key
            else:
                # Active question is answered - start traversal from it (skip it and continue)
                start_question = session.active_question_key
                skip_until = session.active_question_key
        else:
            # Start from first question in tree
            start_question = session.question_index[0].get("name")

        if not start_question:
            return None

        # Traverse tree starting from determined point
        visited = set()
        to_visit = [start_question]
        skip_mode = skip_until is not None

        while to_visit:
            question_name = to_visit.pop(0)
            if question_name in visited:
                continue
            visited.add(question_name)

            # If we're in skip mode, skip until we reach the active question, then continue normally
            if skip_mode:
                if question_name == skip_until:
                    skip_mode = False
                # Don't return this question as unanswered, but still process its edges
                # to determine the next question based on its answer
            else:
                # Check if this question is unanswered
                if question_name not in session.get_answered_questions():
                    return question_name

            # Get question config
            question_config = question_map.get(question_name)
            if not question_config:
                continue
            
            # Check branches to find next questions
            branches = question_config.get("branches", [])
            if branches:
                # Evaluate conditions to find matching branch
                branch_matched = False
                for branch in branches:
                    condition = branch.get("condition", {})
                    # Question is implicit - condition always evaluates against question_name
                    if QuestionBranchEvaluator.matches(condition, session, implicit_question=question_name):
                        target = branch.get("target")
                        if not target:
                            continue
                        
                        # Check if target is a state target
                        if self._is_state_target(target):
                            await self._handle_state_target(target, session, interview_action)
                            return None
                        
                        # Regular question target
                        if target not in visited:
                            to_visit.append(target)
                            branch_matched = True
                            break
                
                # If no branch matched, check default_next or fall back to sequential flow
                if not branch_matched:
                    default_next = question_config.get("default_next")
                    if default_next:
                        if self._is_state_target(default_next):
                            await self._handle_state_target(default_next, session, interview_action)
                            return None
                        if default_next not in visited:
                            to_visit.append(default_next)
                    else:
                        # No default_next specified, fall back to sequential flow (next question in list)
                        current_idx = next(
                            (i for i, q in enumerate(session.question_index) if q.get("name") == question_name),
                            -1
                        )
                        if current_idx >= 0 and current_idx + 1 < len(session.question_index):
                            next_question = session.question_index[current_idx + 1].get("name")
                            if next_question and next_question not in visited:
                                to_visit.append(next_question)
            else:
                # No branches, check default_next or next in list
                default_next = question_config.get("default_next")
                if default_next:
                    if self._is_state_target(default_next):
                        await self._handle_state_target(default_next, session, interview_action)
                        return None
                    if default_next not in visited:
                        to_visit.append(default_next)
                else:
                    # Linear flow - find next question in list
                    current_idx = next(
                        (i for i, q in enumerate(session.question_index) if q.get("name") == question_name),
                        -1
                    )
                    if current_idx >= 0 and current_idx + 1 < len(session.question_index):
                        next_question = session.question_index[current_idx + 1].get("name")
                        if next_question and next_question not in visited:
                            to_visit.append(next_question)

        return None

    async def get_reachable_unanswered_questions(
        self,
        session: InterviewSession,
        interview_action: Optional[Any] = None
    ) -> List[str]:
        """Get all unanswered questions reachable on the current walk path.
        
        Traverses the question graph from current position, following conditional
        branches based on current responses, and returns all unanswered questions
        that are reachable on this path.
        
        Args:
            session: Interview session
            interview_action: Optional InterviewInteractAction to get QuestionNodes from
            
        Returns:
            List of question names that are reachable and unanswered
        """
        if not session.question_index:
            return []
        
        # Build a map of question names to configs
        question_map = {q.get("name"): q for q in session.question_index if q.get("name")}
        
        # Determine starting point
        unanswered = set(session.get_unanswered_questions())
        start_question = None
        skip_until = None
        
        if session.active_question_key:
            if session.active_question_key in unanswered:
                # Continue from unanswered active question
                start_question = session.active_question_key
            else:
                # Active question is answered - start traversal from it (skip it and continue)
                start_question = session.active_question_key
                skip_until = session.active_question_key
        else:
            # Start from first question in tree
            start_question = session.question_index[0].get("name")
        
        if not start_question:
            return []
        
        # Traverse tree starting from determined point and collect all unanswered questions
        visited = set()
        to_visit = [start_question]
        skip_mode = skip_until is not None
        reachable_unanswered = []
        
        while to_visit:
            question_name = to_visit.pop(0)
            if question_name in visited:
                continue
            visited.add(question_name)
            
            # If we're in skip mode, skip until we reach the active question, then continue normally
            if skip_mode:
                if question_name == skip_until:
                    skip_mode = False
                # Don't add this question to results, but still process its edges
                # to determine the next question based on its answer
            else:
                # Check if this question is unanswered and add to results
                if question_name not in session.get_answered_questions():
                    reachable_unanswered.append(question_name)
            
            # Get question config
            question_config = question_map.get(question_name)
            if not question_config:
                continue
            
            # Check branches to find next questions
            branches = question_config.get("branches", [])
            if branches:
                # Evaluate conditions to find matching branch
                branch_matched = False
                for branch in branches:
                    condition = branch.get("condition", {})
                    # Question is implicit - condition always evaluates against question_name
                    if QuestionBranchEvaluator.matches(condition, session, implicit_question=question_name):
                        target = branch.get("target")
                        if not target:
                            continue
                        
                        # Check if target is a state target
                        if self._is_state_target(target):
                            # Stop traversal at state targets
                            continue
                        
                        # Regular question target
                        if target not in visited:
                            to_visit.append(target)
                            branch_matched = True
                            break
                
                # If no branch matched, check default_next or fall back to sequential flow
                if not branch_matched:
                    default_next = question_config.get("default_next")
                    if default_next:
                        if not self._is_state_target(default_next) and default_next not in visited:
                            to_visit.append(default_next)
                    else:
                        # No default_next specified, fall back to sequential flow (next question in list)
                        current_idx = next(
                            (i for i, q in enumerate(session.question_index) if q.get("name") == question_name),
                            -1
                        )
                        if current_idx >= 0 and current_idx + 1 < len(session.question_index):
                            next_question = session.question_index[current_idx + 1].get("name")
                            if next_question and next_question not in visited:
                                to_visit.append(next_question)
            else:
                # No branches, check default_next or next in list
                default_next = question_config.get("default_next")
                if default_next:
                    if not self._is_state_target(default_next) and default_next not in visited:
                        to_visit.append(default_next)
                else:
                    # Linear flow - find next question in list
                    current_idx = next(
                        (i for i, q in enumerate(session.question_index) if q.get("name") == question_name),
                        -1
                    )
                    if current_idx >= 0 and current_idx + 1 < len(session.question_index):
                        next_question = session.question_index[current_idx + 1].get("name")
                        if next_question and next_question not in visited:
                            to_visit.append(next_question)
        
        return reachable_unanswered

    async def _get_question_node_by_name(
        self,
        question_name: str,
        session: InterviewSession,
        interview_action: Optional[Any] = None
    ) -> Optional[QuestionNode]:
        """Get QuestionNode by question name.

        Args:
            question_name: Name of the question
            session: Interview session
            interview_action: Optional InterviewStateInteractAction to search from

        Returns:
            QuestionNode if found, None otherwise
        """
        # Check cache first (stored in session context)
        if session.context is None:
            session.context = {}
        
        node_cache = session.context.get("_question_node_cache", {})
        if question_name in node_cache:
            cached_node_id = node_cache[question_name]
            try:
                from jvspatial.core import Node
                cached_node = await Node.get(cached_node_id)
                if cached_node and isinstance(cached_node, QuestionNode):
                    return cached_node
                else:
                    # Cache entry is stale, remove it
                    del node_cache[question_name]
            except Exception:
                # Cache entry is invalid, remove it
                del node_cache[question_name]
        
        # If we have interview_action, search from there
        if interview_action:
            question_nodes = await interview_action.nodes(direction="out", node=QuestionNode)
            for node in question_nodes:
                if node.label == question_name:
                    # Cache the node
                    node_cache[question_name] = node.id
                    session.context["_question_node_cache"] = node_cache
                    return node

        # Try from current_question if available
        if self.current_question:
            # Try to find from current question's connections
            connected_nodes = await self.current_question.nodes(direction="out", node=QuestionNode)
            for node in connected_nodes:
                if node.label == question_name:
                    # Cache the node
                    node_cache[question_name] = node.id
                    session.context["_question_node_cache"] = node_cache
                    return node

            # Also check incoming connections (bidirectional)
            connected_nodes = await self.current_question.nodes(direction="in", node=QuestionNode)
            for node in connected_nodes:
                if node.label == question_name:
                    # Cache the node
                    node_cache[question_name] = node.id
                    session.context["_question_node_cache"] = node_cache
                    return node

        return None

    async def should_process_question(
        self,
        question_name: str,
        session: InterviewSession
    ) -> bool:
        """Check if a question should be processed given current session state.

        A question should be processed if:
        1. It's in the question_index (exists)
        2. It's reachable via conditional edges from answered questions
        3. No conditional edge skips it based on current answers

        This method traverses the question graph from the root, evaluating
        branch conditions based on current session.responses to determine
        if the target question is on a reachable path.

        Args:
            question_name: Name of the question to check
            session: Interview session with current responses

        Returns:
            True if question should be asked, False if skipped by conditionals
        """
        # Check if question exists in question_index
        question_config = session.get_question_by_name(question_name)
        if not question_config:
            return False

        # If question is already answered, we don't need to process it again
        if question_name in session.get_answered_questions():
            return False

        # Build a map of question names to configs
        question_map = {q.get("name"): q for q in session.question_index if q.get("name")}

        # Start traversal from first question in question_index
        if not session.question_index:
            return False

        # Traverse the graph to see if we can reach this question
        visited = set()
        to_visit = [session.question_index[0].get("name")]

        while to_visit:
            current_question = to_visit.pop(0)
            if current_question in visited:
                continue
            visited.add(current_question)

            # Found the target question - it's reachable
            if current_question == question_name:
                return True

            # Get question config
            current_config = question_map.get(current_question)
            if not current_config:
                continue

            # Check branches to find next questions
            branches = current_config.get("branches", [])
            if branches:
                # For branches, we can only evaluate conditions if prerequisite questions are answered
                # Try to evaluate conditions to find matching branch
                branch_matched = False
                for branch in branches:
                    condition = branch.get("condition", {})
                    # Question is implicit - condition always evaluates against current_question
                    if current_question in session.get_answered_questions():
                        # Condition evaluates against the question that owns the branch
                        if QuestionBranchEvaluator.matches(condition, session, implicit_question=current_question):
                            target = branch.get("target")
                            if target:
                                # Skip state targets - they don't lead to questions
                                if not self._is_state_target(target) and target not in visited:
                                    to_visit.append(target)
                                branch_matched = True
                                break

                # If no branch matched, check default_next or fall back to sequential flow
                if not branch_matched:
                    default_next = current_config.get("default_next")
                    if default_next:
                        # Skip state targets
                        if not self._is_state_target(default_next) and default_next not in visited:
                            to_visit.append(default_next)
                    else:
                        # No default_next specified, fall back to sequential flow (next question in list)
                        current_idx = next(
                            (i for i, q in enumerate(session.question_index) if q.get("name") == current_question),
                            -1
                        )
                        if current_idx >= 0 and current_idx + 1 < len(session.question_index):
                            next_question = session.question_index[current_idx + 1].get("name")
                            if next_question and next_question not in visited:
                                to_visit.append(next_question)
            else:
                # No branches, check default_next or next in list
                # For linear flow, we can continue even if current question is unanswered
                default_next = current_config.get("default_next")
                if default_next:
                    # Skip state targets
                    if not self._is_state_target(default_next) and default_next not in visited:
                        to_visit.append(default_next)
                else:
                    # Linear flow - find next question in list
                    current_idx = next(
                        (i for i, q in enumerate(session.question_index) if q.get("name") == current_question),
                        -1
                    )
                    if current_idx >= 0 and current_idx + 1 < len(session.question_index):
                        next_question = session.question_index[current_idx + 1].get("name")
                        if next_question and next_question not in visited:
                            to_visit.append(next_question)

        # Question not reachable from current state
        return False

    async def process_and_validate(
        self,
        value: Any,
        question_node: QuestionNode,
        session: InterviewSession,
        interaction: Interaction
    ) -> Tuple[Any, ValidationStatus, Optional[str]]:
        """Process input and validate response.

        Args:
            value: Raw input value
            question_node: QuestionNode to process/validate against
            session: Interview session
            interaction: Current interaction

        Returns:
            Tuple of (processed_value, ValidationStatus, optional feedback message)
        """
        # First process the input
        processed_value = await question_node.process_input(value, session, interaction)

        # Then validate (note: validate_response may call process_input again internally,
        # but process_input should be idempotent)
        validation_status, feedback, corrected_value = await question_node.validate_response(processed_value, session)

        # Use corrected value if validator provided one, otherwise use processed value
        final_value = corrected_value if corrected_value is not None else processed_value

        return final_value, validation_status, feedback

    async def get_directive(
        self,
        session: InterviewSession,
        interview_action: Optional[Any] = None
    ) -> Optional[str]:
        """Get directive for the next question.

        Uses session.active_question_key to track position. Updates it when finding next question.

        Args:
            session: Interview session (contains active_question_key for position tracking)
            interview_action: Optional InterviewStateInteractAction to search question nodes from

        Returns:
            Directive string if question found, None otherwise
        """
        # Find next unanswered question (updates session.active_question_key)
        question_node = await self.find_next_question(session, interview_action)
        if not question_node:
            return None

        # Execute question node to get directive
        directive = await question_node.execute(self)
        return directive

    async def get_next_questions(
        self,
        current_question_name: str,
        session: InterviewSession
    ) -> List[str]:
        """Get list of possible next questions based on branches.

        Args:
            current_question_name: Name of current question
            session: Interview session

        Returns:
            List of possible next question names (excludes state targets)
        """
        # Find question config
        question_config = next(
            (q for q in session.question_index if q.get("name") == current_question_name),
            None
        )

        if not question_config:
            return []

        next_questions = []

        # Check branches
        branches = question_config.get("branches", [])
        for branch in branches:
            condition = branch.get("condition", {})
            # Question is implicit - condition always evaluates against current_question_name
            if QuestionBranchEvaluator.matches(condition, session, implicit_question=current_question_name):
                target = branch.get("target")
                if target and not self._is_state_target(target):
                    # Only include question targets, not state targets
                    next_questions.append(target)

        # If no branch matched, check default_next
        if not next_questions:
            default_next = question_config.get("default_next")
            if default_next and not self._is_state_target(default_next):
                next_questions.append(default_next)

        return next_questions
