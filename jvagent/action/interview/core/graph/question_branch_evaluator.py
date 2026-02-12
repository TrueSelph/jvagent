"""Question branch evaluator for interview action.

This module provides unified condition matching logic for conditional question branching.
Supports enhanced operators for flexible condition evaluation.
Also supports custom branch functions for complex condition evaluation.
"""

import inspect
import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

from ..session.interview_session import InterviewSession
from .condition_operators import ConditionOperator

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)


class QuestionBranchEvaluator:
    """Evaluates conditions for conditional question branching.
    
    Provides unified condition matching logic used throughout the interview system.
    Supports enhanced operators for flexible condition evaluation.
    Also supports custom branch functions for complex condition evaluation with
    full access to session data and graph context.
    
    Branch functions are only evaluated after the question is answered to prevent
    premature execution during graph traversal.
    """
    
    @staticmethod
    async def matches(
        condition: Dict[str, Any],
        session: InterviewSession,
        implicit_question: str,
        visitor: Optional["InteractWalker"] = None
    ) -> bool:
        """Check if a condition matches the current session state.

        The question is always implicit from the branch context - conditions evaluate
        against the question that owns the branch.

        Supports condition formats:
        - Equality: {"op": "equals", "value": "yes"}
        - Comparison: {"op": ">=", "value": 18}
        - Existence: {"op": "exists"} (no value needed)
        - Function-based: {"function": "function_name"} (returns bool)
        - Function with operator: {"function": "function_name", "op": ">=", "value": 7}

        Args:
            condition: Condition dict with 'op' and optional 'value' keys, or 'function' key
            session: Interview session
            implicit_question: Required question name - the question that owns this branch
            visitor: Optional InteractWalker for branch function access to graph context

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

        # Check for function-based condition
        if "function" in condition:
            return await QuestionBranchEvaluator._evaluate_function_condition(
                condition, session, question_name, visitor
            )

        # Legacy operator-based condition evaluation
        return QuestionBranchEvaluator._evaluate_operator_condition(
            condition, session, question_name
        )

    @staticmethod
    async def _evaluate_function_condition(
        condition: Dict[str, Any],
        session: InterviewSession,
        question_name: str,
        visitor: Optional["InteractWalker"]
    ) -> bool:
        """Evaluate a function-based branch condition.

        Args:
            condition: Condition dict with 'function' key and optional 'op' and 'value'
            session: Interview session
            question_name: Question name for logging
            visitor: Optional InteractWalker for function access

        Returns:
            True if condition matches, False otherwise
        """
        from ..foundation.decorators import get_branch_function

        function_name = condition.get("function")
        if not function_name:
            logger.warning(
                f"Function-based condition missing 'function' key for question '{question_name}': {condition}"
            )
            return False

        operator = condition.get("op")
        is_existence_check = operator in ("exists", "is_set", "not_exists", "is_not_set")
        if not is_existence_check:
            if question_name not in session.responses:
                logger.debug(
                    f"Function condition check skipped: question '{question_name}' not answered yet. "
                    f"Condition: {condition}"
                )
                return False

        func = get_branch_function(session.interview_type, function_name)
        if not func:
            logger.error(
                f"Branch function '{function_name}' not found for interview type '{session.interview_type}'. "
                f"Question: '{question_name}'"
            )
            return False

        try:
            if inspect.iscoroutinefunction(func):
                result = await func(session, visitor)
            else:
                result = func(session, visitor)

            if "op" in condition:
                expected_value = condition.get("value")
                op = condition.get("op")
                try:
                    evaluation_result = ConditionOperator.evaluate(op, result, expected_value)
                    logger.debug(
                        f"Function '{function_name}' returned {result!r}, operator '{op}' "
                        f"evaluation: {evaluation_result}"
                    )
                    return evaluation_result
                except ValueError as e:
                    logger.warning(
                        f"Invalid operator '{op}' in function condition for '{function_name}': {e}"
                    )
                    return False
            else:
                if not isinstance(result, bool):
                    logger.warning(
                        f"Branch function '{function_name}' returned {type(result).__name__} "
                        f"but bool expected when no operator is specified. Converting to bool."
                    )
                return bool(result)

        except Exception as e:
            logger.error(
                f"Error executing branch function '{function_name}' for question '{question_name}': {e}",
                exc_info=True
            )
            return False


    @staticmethod
    def _evaluate_operator_condition(
        condition: Dict[str, Any],
        session: InterviewSession,
        question_name: str
    ) -> bool:
        """Evaluate a legacy operator-based branch condition.

        Args:
            condition: Condition dict with 'op' and optional 'value' keys
            session: Interview session
            question_name: Question name for logging

        Returns:
            True if condition matches, False otherwise
        """
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
