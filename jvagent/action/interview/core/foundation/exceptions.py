"""Custom exceptions for interview action module.

Centralized exception definitions for consistent error handling.
"""


class InterviewError(Exception):
    """Base exception for interview action errors."""
    pass


class ValidationError(InterviewError):
    """Raised when validation fails.
    
    Attributes:
        field: Name of the field that failed validation
        message: Error message describing the validation failure
        value: The value that failed validation
    """
    def __init__(self, field: str, message: str, value=None):
        self.field = field
        self.message = message
        self.value = value
        super().__init__(f"Validation failed for field '{field}': {message}")


class QuestionNotFoundError(InterviewError):
    """Raised when a question cannot be found.
    
    Attributes:
        question_name: Name of the question that was not found
    """
    def __init__(self, question_name: str):
        self.question_name = question_name
        super().__init__(f"Question '{question_name}' not found in question_graph")


class InvalidStateTransitionError(InterviewError):
    """Raised when an invalid state transition is attempted.
    
    Attributes:
        from_state: Current state
        to_state: Target state that is invalid
    """
    def __init__(self, from_state: str, to_state: str, reason: str = None):
        self.from_state = from_state
        self.to_state = to_state
        self.reason = reason
        msg = f"Invalid state transition: {from_state} -> {to_state}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)


class SessionNotFoundError(InterviewError):
    """Raised when an interview session cannot be found.
    
    Attributes:
        interview_type: Type of interview session that was not found
        conversation_id: Conversation ID where session was expected
    """
    def __init__(self, interview_type: str, conversation_id: str = None):
        self.interview_type = interview_type
        self.conversation_id = conversation_id
        msg = f"Interview session of type '{interview_type}' not found"
        if conversation_id:
            msg += f" in conversation '{conversation_id}'"
        super().__init__(msg)


class ClassificationError(InterviewError):
    """Raised when classification fails.
    
    Attributes:
        message: Error message describing the classification failure
    """
    def __init__(self, message: str):
        self.message = message
        super().__init__(f"Classification failed: {message}")
