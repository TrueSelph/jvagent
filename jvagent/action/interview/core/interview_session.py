"""InterviewSession node for managing interview state."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from jvspatial.core import Node
from jvspatial.core.annotations import attribute

from .validation import InterviewState, ValidationStatus


class InterviewSession(Node):
    """Persistent interview session state node.
    
    Stores the current state of an interview session, including:
    - Current state machine state
    - Question schema/index
    - Collected responses
    - Validation results per question
    - Active question tracking
    - Timestamps
    
    Connected to Conversation via edge for persistence.
    """
    
    # State management
    state: InterviewState = attribute(
        default=InterviewState.ACTIVE,
        description="Current state machine state"
    )
    
    # Question schema
    question_index: List[Dict[str, Any]] = attribute(
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
        description="Validation status per question (VALID/VALID_WITH_FLAG/INVALID)"
    )
    
    # Active question tracking
    active_question_key: Optional[str] = attribute(
        default=None,
        description="Currently active question key (for revisions)"
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
    
    # Reference to conversation
    conversation_id: str = attribute(
        default="",
        description="Reference to parent conversation"
    )
    
    def get_answered_questions(self) -> List[str]:
        """Get list of question keys that have been answered."""
        return list(self.responses.keys())
    
    def get_unanswered_questions(self) -> List[str]:
        """Get list of question keys that haven't been answered."""
        answered = set(self.get_answered_questions())
        all_questions = [q.get("name", "") for q in self.question_index if q.get("name")]
        return [q for q in all_questions if q and q not in answered]
    
    def get_required_questions(self) -> List[str]:
        """Get list of required question keys."""
        return [
            q.get("name", "")
            for q in self.question_index
            if q.get("name") and q.get("required", False)
        ]
    
    def has_all_required_answers(self) -> bool:
        """Check if all required questions have been answered."""
        required = set(self.get_required_questions())
        answered = set(self.get_answered_questions())
        return required.issubset(answered)
    
    def get_response(self, question_key: str) -> Any:
        """Get response for a specific question."""
        return self.responses.get(question_key)
    
    def set_response(self, question_key: str, value: Any) -> None:
        """Set response for a question."""
        self.responses[question_key] = value
    
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

