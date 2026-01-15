"""Question branch evaluator for interview action.

This module provides unified condition matching logic for conditional question branching.
"""

import logging
from typing import Any, Dict

from .interview_session import InterviewSession

logger = logging.getLogger(__name__)


class QuestionBranchEvaluator:
    """Evaluates conditions for conditional question branching.
    
    Provides unified condition matching logic used throughout the interview system.
    """
    
    @staticmethod
    def matches(
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
        
        # Check if the question has been answered
        if question_name not in session.get_answered_questions():
            return False
        
        actual_value = session.responses.get(question_name)
        if actual_value is None:
            return False
        
        # Normalize values for comparison (handle strings with case-insensitive and whitespace)
        def normalize_value(value: Any) -> Any:
            if isinstance(value, str):
                return value.strip().lower()
            return value
        
        # Compare normalized values
        normalized_actual = normalize_value(actual_value)
        normalized_expected = normalize_value(expected_value)
        
        return normalized_actual == normalized_expected
