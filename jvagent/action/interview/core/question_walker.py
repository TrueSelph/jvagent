"""QuestionWalker for traversing QuestionNodes in tree-based interview flows.

This module provides QuestionWalker, a specialized walker that traverses
QuestionNodes based on conditional edges, triggers validations/handlers,
and returns directives to InterviewStateInteractAction.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from jvspatial.core import Walker
from jvagent.memory import Interaction

from .interview_session import InterviewSession
from .question_node import QuestionNode
from .validation import ValidationStatus

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
    
    async def find_next_question(
        self, 
        session: InterviewSession,
        interview_action: Optional[Any] = None,
        start_from: Optional[str] = None
    ) -> Optional[QuestionNode]:
        """Find next unanswered question based on tree traversal.
        
        Traverses the question tree starting from session.active_question_key (if set),
        or from the first unanswered question, respecting conditional branches.
        Updates session.active_question_key with the found question.
        
        Args:
            session: Interview session (contains active_question_key for position tracking)
            interview_action: Optional InterviewStateInteractAction to search question nodes from
            start_from: Optional question name to start traversal from (overrides session.active_question_key)
            
        Returns:
            Next QuestionNode to ask, or None if all questions answered
        """
        unanswered = session.get_unanswered_questions()
        if not unanswered:
            session.active_question_key = None
            await session.save()
            return None
        
        # Determine starting point: start_from > session.active_question_key > first unanswered
        if start_from:
            current_question_name = start_from
        elif session.active_question_key and session.active_question_key in unanswered:
            # Continue from where we left off
            current_question_name = session.active_question_key
        else:
            # Find first unanswered question by traversing tree from root
            current_question_name = await self._find_first_unanswered_in_tree(session)
            if not current_question_name:
                session.active_question_key = None
                await session.save()
                return None
        
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
        
        return None
    
    async def _find_first_unanswered_in_tree(
        self, 
        session: InterviewSession
    ) -> Optional[str]:
        """Find first unanswered question by traversing tree from root.
        
        If session.active_question_key is set (even if answered), continues traversal
        from that position. Otherwise starts from the first question in question_index.
        
        Args:
            session: Interview session (may contain active_question_key for position)
            
        Returns:
            Name of first unanswered question, or None
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
                # Continue to next iteration to skip this question
                # But still process its branches/default_next
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
                for branch in branches:
                    condition = branch.get("condition", {})
                    if self._condition_matches(condition, session):
                        target = branch.get("target")
                        if target and target not in visited:
                            to_visit.append(target)
                            break
                else:
                    # No branch matched, check default_next
                    default_next = question_config.get("default_next")
                    if default_next and default_next not in visited:
                        to_visit.append(default_next)
            else:
                # No branches, check default_next or next in list
                default_next = question_config.get("default_next")
                if default_next and default_next not in visited:
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
        # If we have interview_action, search from there
        if interview_action:
            question_nodes = await interview_action.nodes(direction="out", node=QuestionNode)
            for node in question_nodes:
                if node.label == question_name:
                    return node
        
        # Try from current_question if available
        if self.current_question:
            # Try to find from current question's connections
            connected_nodes = await self.current_question.nodes(direction="out", node=QuestionNode)
            for node in connected_nodes:
                if node.label == question_name:
                    return node
            
            # Also check incoming connections (bidirectional)
            connected_nodes = await self.current_question.nodes(direction="in", node=QuestionNode)
            for node in connected_nodes:
                if node.label == question_name:
                    return node
        
        return None
    
    def _condition_matches(
        self,
        condition: Dict[str, Any],
        session: InterviewSession
    ) -> bool:
        """Check if a condition matches the current session state.
        
        Args:
            condition: Condition dict with 'question' and 'equals' keys
            session: Interview session
            
        Returns:
            True if condition matches, False otherwise
        """
        if not condition:
            return False
        
        question_name = condition.get("question")
        expected_value = condition.get("equals")
        
        if not question_name or expected_value is None:
            return False
        
        actual_value = session.responses.get(question_name)
        return actual_value == expected_value
    
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
        # Process input first (handles custom transformations)
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
            List of possible next question names
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
            if self._condition_matches(condition, session):
                target = branch.get("target")
                if target:
                    next_questions.append(target)
        
        # If no branch matched, check default_next
        if not next_questions:
            default_next = question_config.get("default_next")
            if default_next:
                next_questions.append(default_next)
        
        return next_questions

