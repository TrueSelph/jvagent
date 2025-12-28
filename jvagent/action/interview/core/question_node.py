"""QuestionNode for interview question handling.

This module provides QuestionNode, a node that represents individual interview questions
in the interview process with validation capabilities.
"""

import logging
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from jvspatial.core import Node
from jvspatial.core.annotations import attribute

from .validation import ValidationStatus

if TYPE_CHECKING:
    from .interview_session import InterviewSession
    from .interview_walker import InterviewWalker

logger = logging.getLogger(__name__)


class QuestionNode(Node):
    """Node representing an individual interview question.
    
    Each QuestionNode represents a single question in the interview flow with:
    - Question text and constraints
    - Three-tier validation (VALID, VALID_WITH_FLAG, INVALID)
    - Required vs optional flags
    - Validation rules embedded in constraints
    - Template for generating question prompts
    """
    
    description: str = "Interview question node for gathering user information"
    
    directive_template: Optional[str] = attribute(
        default="""
    Tailor your response to get the information needed based on the following description:
    {description}
    Avoid asking for other information not related to this description unless specified elsewhere.  {question}

    {instructions}
    """,
        description="Optional template for formatting the directive. Uses default structured format if not provided.",
    )
    
    instructions_template: Optional[str] = attribute(
        default="Take note of the following additional instructions while responding to the user but avoid mentioning them unless it is needed:\n {instructions}",
        description="Optional instructions for the directive.",
    )
    
    state: Dict[str, Any] = attribute(
        default={},
        description="Question configuration containing 'name', 'question', 'constraints', and 'required'",
    )
    
    label: str = attribute(
        default_factory=str,
        description="Label for the node (typically the question name)",
    )

    async def on_register(self) -> None:
        """Register the node."""
        logger.debug(f"QuestionNode registered with state: {self.state}")

    async def execute(self, walker: "InterviewWalker") -> Optional[str]:
        """Execute question node to check if info is needed and return directive.

        Args:
            walker: The InterviewWalker visiting this node

        Returns:
            Directive string if information is needed, None otherwise
        """
        logger.debug(f"QuestionNode executed for {self.label}")

        if not self.state.get("name", ""):
            logger.debug("No name in state")
            return None

        # Check if this question has been answered
        question_key = self.state.get("name", "")
        session = walker.interview_session
        
        if session and question_key in session.get_answered_questions():
            logger.debug(f"QuestionNode: {self.label} already answered")
            return None

        constraints = self.state.get("constraints", {})
        question = self.state.get("question", "")
        description = constraints.get("description", "")
        instructions = constraints.get("instructions", "")

        if instructions:
            instructions = self.instructions_template.format(instructions=instructions)

        directive = self.directive_template.format(
            description=description,
            instructions=instructions,
            question=question,
        )
        
        if directive:
            return directive
        else:
            logger.debug("QuestionNode got no directive, something went wrong")
            return None

    async def validate_response(
        self, 
        value: Any, 
        session: "InterviewSession"
    ) -> Tuple[ValidationStatus, Optional[str]]:
        """Validate a response value against this question's constraints.
        
        Returns (validation_status, feedback_message)
        
        Status can be:
        - VALID: Response meets all constraints, store and continue
        - VALID_WITH_FLAG: Response is acceptable but needs clarification (e.g., "next Tuesday")
        - INVALID: Response doesn't meet constraints, needs correction
        
        Args:
            value: The extracted response value
            session: The interview session for context
            
        Returns:
            Tuple of (ValidationStatus, optional feedback message)
        """
        constraints = self.state.get("constraints", {})
        question_key = self.state.get("name", "")
        
        # Check if value is empty/None
        if value is None or (isinstance(value, str) and not value.strip()):
            if self.state.get("required", False):
                return ValidationStatus.INVALID, "This field is required."
            return ValidationStatus.VALID, None  # Optional field can be empty
        
        # Type validation
        expected_type = constraints.get("type", "string")
        if expected_type == "string" and not isinstance(value, str):
            return ValidationStatus.INVALID, f"Expected a string value, got {type(value).__name__}"
        elif expected_type == "number" or expected_type == "integer":
            try:
                float(value) if expected_type == "number" else int(value)
            except (ValueError, TypeError):
                return ValidationStatus.INVALID, f"Expected a {expected_type} value"
        
        # Pattern/regex validation
        pattern = constraints.get("pattern")
        if pattern and isinstance(value, str):
            if not re.match(pattern, value):
                return ValidationStatus.INVALID, constraints.get("pattern_error", "Value doesn't match required format")
        
        # Email validation
        if constraints.get("format") == "email" and isinstance(value, str):
            email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            if not re.match(email_pattern, value):
                return ValidationStatus.INVALID, "Please provide a valid email address"
        
        # Custom validation function (if provided)
        validator = constraints.get("validator")
        if validator:
            # If validator is a callable, call it
            if callable(validator):
                try:
                    result = validator(value, session)
                    if isinstance(result, tuple):
                        status, message = result
                        return ValidationStatus(status) if isinstance(status, str) else status, message
                    elif isinstance(result, bool):
                        return ValidationStatus.VALID if result else ValidationStatus.INVALID, None
                except Exception as e:
                    logger.warning(f"Validator function raised exception: {e}")
                    return ValidationStatus.INVALID, f"Validation error: {str(e)}"
        
        # Check for ambiguous values that might need clarification
        # This is a heuristic - can be customized per question
        ambiguous_patterns = constraints.get("ambiguous_patterns", [])
        if isinstance(value, str):
            value_lower = value.lower()
            for pattern in ambiguous_patterns:
                if pattern in value_lower:
                    feedback = constraints.get("ambiguous_feedback", "I'd like to clarify this.")
                    return ValidationStatus.VALID_WITH_FLAG, feedback
        
        # Check for common ambiguous time expressions
        if constraints.get("type") == "datetime" or "time" in question_key.lower() or "date" in question_key.lower():
            time_ambiguous = ["next", "this", "tomorrow", "today", "soon", "later"]
            if isinstance(value, str) and any(ambiguous in value.lower() for ambiguous in time_ambiguous):
                return ValidationStatus.VALID_WITH_FLAG, "Got it. Let me clarify the specific time."
        
        # All checks passed
        return ValidationStatus.VALID, None

