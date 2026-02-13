"""InterviewSession node for managing interview state."""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.core import Node
from jvspatial.core.annotations import attribute

from ..foundation.enums import InterviewState, ValidationStatus

if TYPE_CHECKING:
    from ..graph.interview_walker import InterviewWalker
    from ..graph.question_node import QuestionNode


class InterviewSession(Node):
    """Persistent interview session state node.
    
    Stores the current state of an interview session, including:
    - Interview type (class name) for filtering
    - Current state machine state
    - Question schema/index
    - Collected responses
    - Validation results per question
    - Active question tracking
    - Timestamps
    
    Connected to Conversation via edge for per-user persistence.
    No edges to InterviewInteractAction (actions can be destroyed/rebuilt).
    """
    
    # Interview type identification
    interview_type: str = attribute(
        default="",
        description="Class name of the InterviewInteractAction (e.g., 'RegistrationInterviewAction')"
    )
    
    # State management
    state: InterviewState = attribute(
        default=InterviewState.ACTIVE,
        description="Current state machine state"
    )
    
    # Question schema (same structure as action's question_graph)
    question_graph: List[Dict[str, Any]] = attribute(
        default_factory=list,
        description="List of question configurations (schema)"
    )
    
    # Response storage
    responses: Dict[str, Any] = attribute(
        default_factory=dict,
        description="Collected responses keyed by question name"
    )
    
    # Validation tracking
    validation_results: Dict[str, str] = attribute(
        default_factory=dict,
        description="Validation status per question (VALID/INVALID)"
    )
    
    # Context storage for arbitrary data (user-specific state)
    context: Dict[str, Any] = attribute(
        default_factory=dict,
        description="Arbitrary context data for storing intermediate processing results, flags, or other state"
    )
    
    # Update queue for pending updates (replaces update_history)
    update_queue: List[Dict[str, Any]] = attribute(
        default_factory=list,
        description="Queue of pending updates: [{field, value, old_value}] in graph order"
    )
    
    # Target node tracking (user-specific state)
    target_node: Optional[str] = attribute(
        default=None,
        description="Node ID of the current target node (QuestionNode, StateNode, or InterviewInteractAction). Determines where the walker will spawn on next interaction. Updated after intent classification and during walker traversal."
    )
    
    # Timestamps
    started_at: Optional[datetime] = attribute(
        default=None,
        description="Session start timestamp"
    )
    completed_at: Optional[datetime] = attribute(
        default=None,
        description="Session completion timestamp"
    )
    # Last modified timestamp used for caching and pruning decisions
    last_modified: Optional[datetime] = attribute(
        default=None,
        description="Last modified timestamp for session persistence and cache invalidation"
    )
    
    # Reference to conversation
    conversation_id: str = attribute(
        default="",
        description="Reference to parent conversation"
    )
    
    # Auto-confirm flag for skipping REVIEW confirmation prompt
    auto_confirm: bool = attribute(
        default=False,
        description="When True, skip REVIEW confirmation prompt and proceed directly to COMPLETED"
    )
    
    def get_answered_questions(self) -> List[str]:
        """Get list of question keys that have been answered.

        Forces a fresh read from the responses dict to avoid any caching issues
        with jvspatial attributes.
        """
        # Access responses dict and create explicit snapshot to bypass any caching
        responses_dict = dict(self.responses) if self.responses else {}
        return list(responses_dict.keys())

    def get_unanswered_questions(self) -> List[str]:
        """Get list of question keys that haven't been answered.

        Forces a fresh read from the responses dict to avoid any caching issues
        with jvspatial attributes.
        """
        # Access responses dict directly and create explicit snapshot
        responses_dict = dict(self.responses) if self.responses else {}
        answered = set(responses_dict.keys())
        all_questions = [q.get("name", "") for q in self.question_graph if q.get("name")]
        return [q for q in all_questions if q and q not in answered]
    
    def get_required_questions(self) -> List[str]:
        """Get list of required question keys."""
        return [
            q.get("name", "")
            for q in self.question_graph
            if q.get("name") and q.get("required", False)
        ]
    
    async def has_all_required_answers(self, interview_walker: Optional["InterviewWalker"] = None) -> bool:
        """Check if all required questions have been answered.
        
        If interview_walker is provided, only checks required questions that are
        reachable on the current conditional path. Otherwise, checks all required
        questions.
        
        Args:
            interview_walker: Optional InterviewWalker to determine reachable questions
            
        Returns:
            True if all required (and reachable) questions have been answered
        """
        if interview_walker:
            # Only check required questions on the active conditional path
            required = await interview_walker.get_reachable_required_questions(self)
        else:
            # Check all required questions
            required = set(self.get_required_questions())
        
        answered = set(self.get_answered_questions())
        return required.issubset(answered)
    
    async def get_required_questions_on_path(
        self,
        interview_walker: "InterviewWalker"
    ) -> List[str]:
        """Get list of required question keys that are reachable on the current conditional path.
        
        Traverses the question graph from root following active conditional branches
        and returns only required questions that are reachable given current responses.
        
        Args:
            interview_walker: InterviewWalker instance to determine reachable questions
            
        Returns:
            List of required question names that are reachable on the current path
        """
        reachable_required = await interview_walker.get_reachable_required_questions(self)
        return list(reachable_required)
    
    def get_response(self, question_key: str) -> Any:
        """Get response for a specific question."""
        return self.responses.get(question_key)
    
    def set_response(self, question_key: str, value: Any) -> None:
        """Set response for a question."""
        self.responses[question_key] = value
    
    def pop_update(self, field: str) -> Optional[Dict[str, Any]]:
        """Remove and return queue entry for field, if present."""
        for i, entry in enumerate(self.update_queue):
            if entry["field"] == field:
                return self.update_queue.pop(i)
        return None

    def has_pending_update(self, field: str) -> bool:
        """Check if field has a pending update in the queue."""
        return any(e["field"] == field for e in self.update_queue)

    def can_update_field(self, field: str) -> bool:
        """Check if field is updateable (is it answered?).
        
        Args:
            field: Field name to check
            
        Returns:
            True if field has been answered and can be updated
        """
        return field in self.responses
    
    def set_validation_status(self, question_key: str, status: ValidationStatus) -> None:
        """Set validation status for a question."""
        self.validation_results[question_key] = status.value
    
    def get_validation_status(self, question_key: str) -> Optional[ValidationStatus]:
        """Get validation status for a question."""
        status_str = self.validation_results.get(question_key)
        if status_str:
            try:
                return ValidationStatus(status_str)
            except ValueError:
                return None
        return None
    
    def transition_to(self, new_state: InterviewState) -> None:
        """Transition to a new state."""
        self.state = new_state
        if new_state == InterviewState.COMPLETED and not self.completed_at:
            self.completed_at = datetime.now()
    
    async def reset(self) -> None:
        """Reset session to initial state, clearing all responses.

        Keeps interview_type and conversation_id intact.
        """
        self.state = InterviewState.ACTIVE
        self.responses = {}
        self.validation_results = {}
        self.context = {}
        self.update_queue = []
        self.target_node = None
        self.completed_at = None
        await self.save()

    async def save(self, *args, **kwargs):
        """Override save to update last_modified timestamp before persisting."""
        try:
            self.last_modified = datetime.now()
        except Exception:
            # Best-effort; do not block save on timestamp issues
            pass
        # If batching is enabled, mark that changes occurred and defer actual save
        if getattr(self, "_batching", False):
            setattr(self, "_batched_changes", True)
            return None

        # Call Node.save() dynamically to avoid direct import issues
        try:
            return await super().save(*args, **kwargs)
        except Exception:
            # Some tests or contexts may not have async save behavior; re-raise
            raise
    
    async def cleanup(self, cascade: bool = False) -> None:
        """Cleanup session data and edges.
        
        Call this when session data is no longer needed (typically after
        data has been processed and stored elsewhere).
        
        This removes the session from the graph entirely.
        Uses cascade=False by default for reliable deletion of session and edges.
        """
        await self.delete(cascade=cascade)
    
    def get_question_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Get question configuration by name.
        
        Args:
            name: Question name
            
        Returns:
            Question configuration dict if found, None otherwise
        """
        return next(
            (q for q in self.question_graph if q.get("name") == name),
            None
        )
    
    async def get_next_questions(self, current_question: str, visitor: Optional[Any] = None, interview_action: Optional[Any] = None) -> List[str]:
        """Get possible next questions based on branches.
        
        Args:
            current_question: Name of current question
            visitor: Optional InteractWalker for branch function access
            
        Returns:
            List of possible next question names
        """
        question_config = self.get_question_by_name(current_question)
        if not question_config:
            return []
        
        next_questions = []
        branches = question_config.get("branches", [])
        
        # Check branches for matching conditions
        # Question is implicit - condition evaluates against the question that owns this branch
        from ..graph.question_branch_evaluator import QuestionBranchEvaluator
        for branch in branches:
            condition = branch.get("condition", {})
            
            # Use QuestionBranchEvaluator for proper evaluation
            if await QuestionBranchEvaluator.matches(condition, self, implicit_question=question_config.get("name"), visitor=visitor, interview_action=interview_action):
                target = branch.get("target")
                if target:
                    next_questions.append(target)
        
        # If no branch matched, check default_next
        if not next_questions:
            default_next = question_config.get("default_next")
            if default_next:
                next_questions.append(default_next)
        
        return next_questions
    
    async def get_reachable_questions(
        self,
        first_node: "QuestionNode",
        visitor: Optional[Any] = None,
        interview_action: Optional[Any] = None
    ) -> set:
        """Get questions reachable on the current branch path.
        
        Uses QuestionPathWalker to traverse the question graph following
        conditional branches (using BranchCache) and returns the set of
        question names that are reachable given current responses.
        
        Args:
            first_node: The first QuestionNode in the graph (entry point)
            visitor: Optional InteractWalker for branch function evaluation
            
        Returns:
            Set of question names reachable on the active branch path
        """
        from ..graph.question_path_walker import QuestionPathWalker
        return await QuestionPathWalker.get_reachable_questions(
            self, first_node, visitor, interview_action
        )

    async def get_next_unanswered_on_path(
        self,
        first_node: "QuestionNode",
        visitor: Optional[Any] = None,
        interview_action: Optional[Any] = None
    ) -> Optional["QuestionNode"]:
        """Get next unanswered question on the active path.
        
        Uses QuestionPathWalker to find the next unanswered question
        following the active branch path (using BranchCache).
        
        Args:
            first_node: The first QuestionNode in the graph (entry point)
            visitor: Optional InteractWalker for branch function evaluation
            
        Returns:
            Next unanswered QuestionNode on the active path, or None if all answered
        """
        from ..graph.question_path_walker import QuestionPathWalker
        return await QuestionPathWalker.find_next_target(
            self, first_node, visitor, interview_action
        )
    
    def extract_data(self) -> Dict[str, Any]:
        """Extract collected data for external processing.
        
        Returns:
            Dictionary of question responses ready for processing
        """
        return {
            "interview_type": self.interview_type,
            "responses": self.responses.copy(),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "validation_results": self.validation_results.copy(),
        }

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def batch_save(self):
        """Async context manager to batch multiple changes and perform a single save.

        While inside the context, calls to `await session.save()` will be deferred
        and only a single save will be performed when the context exits (if any
        changes occurred). This reduces redundant persistence operations.
        """
        # Enable batching
        setattr(self, "_batching", True)
        setattr(self, "_batched_changes", False)
        try:
            yield
        finally:
            # Disable batching and flush a single save if changes occurred
            setattr(self, "_batching", False)
            if getattr(self, "_batched_changes", False):
                try:
                    await super(InterviewSession, self).save()
                finally:
                    setattr(self, "_batched_changes", False)

