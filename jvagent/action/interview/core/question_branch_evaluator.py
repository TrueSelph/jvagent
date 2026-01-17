"""Question branch evaluator for interview action.

This module provides unified condition matching logic for conditional question branching.
Supports enhanced operators for flexible condition evaluation.
"""

import logging
from typing import Any, Dict

from .interview_session import InterviewSession
from .condition_operators import ConditionOperator

logger = logging.getLogger(__name__)


class QuestionBranchEvaluator:
    """Evaluates conditions for conditional question branching.
    
    Provides unified condition matching logic used throughout the interview system.
    Supports enhanced operators for flexible condition evaluation.
    """
    
    @staticmethod
    def matches(
        condition: Dict[str, Any],
        session: InterviewSession,
        implicit_question: str
    ) -> bool:
        """Check if a condition matches the current session state.

        The question is always implicit from the branch context - conditions evaluate
        against the question that owns the branch.

        Supports condition formats:
        - Equality: {"op": "equals", "value": "yes"}
        - Comparison: {"op": ">=", "value": 18}
        - Existence: {"op": "exists"} (no value needed)

        Args:
            condition: Condition dict with 'op' and optional 'value' keys (question is implicit)
            session: Interview session
            implicit_question: Required question name - the question that owns this branch

        Returns:
            True if condition matches, False otherwise
        """
        if not condition:
            return False

        # Question is always implicit from branch context
        if not implicit_question:
            logger.error("implicit_question is required for branch condition evaluation")
            return False

        question_name = implicit_question

        # Get operator from condition
        operator = condition.get("op")
        if not operator:
            logger.warning(
                f"No 'op' field found in condition for question '{question_name}': {condition}"
            )
            return False

        # Check if the question has been answered (unless checking existence)
        if operator not in ("exists", "is_set", "not_exists", "is_not_set"):
            if question_name not in session.get_answered_questions():
                logger.debug(
                    f"Condition check failed: question '{question_name}' not answered yet. "
                    f"Condition: {condition}"
                )
                return False

        actual_value = session.responses.get(question_name)

        # Handle existence operators (don't require value to be set)
        if operator in ("exists", "is_set", "not_exists", "is_not_set"):
            try:
                result = ConditionOperator.evaluate(operator, actual_value)
                logger.debug(
                    f"Existence operator '{operator}' evaluation for '{question_name}': "
                    f"actual_value={actual_value}, result={result}"
                )
                return result
            except ValueError:
                logger.warning(f"Invalid operator '{operator}' in condition for question '{question_name}'")
                return False

        # For other operators, we need a value to compare
        if actual_value is None:
            logger.debug(
                f"Condition check failed: question '{question_name}' has no value. "
                f"Condition: {condition}"
            )
            return False

        # Get expected value
        expected_value = condition.get("value")

        try:
            result = ConditionOperator.evaluate(operator, actual_value, expected_value)
            logger.debug(
                f"Operator '{operator}' evaluation for '{question_name}': "
                f"actual_value={actual_value!r}, expected_value={expected_value!r}, result={result}"
            )
            return result
        except ValueError as e:
            logger.warning(f"Invalid operator '{operator}' in condition for question '{question_name}': {e}")
            return False
