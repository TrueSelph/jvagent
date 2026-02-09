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
        """Evaluate a function-based branch condition with caching support.
        
        Transparently caches computed results and only re-executes if dependencies change.
        This avoids expensive function calls when response values haven't been modified.

        Args:
            condition: Condition dict with 'function' key and optional 'op' and 'value'
            session: Interview session
            question_name: Question name for logging
            visitor: Optional InteractWalker for function access

        Returns:
            True if condition matches, False otherwise
        """
        from ..foundation.decorators import get_branch_function
        from ..utils.cache_utils import BranchFunctionCache

        function_name = condition.get("function")
        if not function_name:
            logger.warning(
                f"Function-based condition missing 'function' key for question '{question_name}': {condition}"
            )
            return False

        # Check if question is answered (unless checking existence)
        # For function-based conditions, we need the question to be answered
        # to have meaningful data for the function to evaluate.
        # This prevents premature function execution during graph traversal.
        operator = condition.get("op")
        is_existence_check = operator in ("exists", "is_set", "not_exists", "is_not_set")
        
        if not is_existence_check:
            if question_name not in session.responses:
                logger.debug(
                    f"Function condition check skipped: question '{question_name}' not answered yet. "
                    f"Condition: {condition}"
                )
                return False

        # Look up function from registry
        func = get_branch_function(session.interview_type, function_name)
        if not func:
            logger.error(
                f"Branch function '{function_name}' not found for interview type '{session.interview_type}'. "
                f"Question: '{question_name}'"
            )
            return False

        # Try to get cached result (check cache before executing)
        branch_cache = BranchFunctionCache(session)
        cache_key = branch_cache._make_cache_key(question_name, condition, function_name)
        cached_entry = branch_cache.get(cache_key)
        
        if cached_entry:
            # Dependencies haven't changed, use cached result
            cached_result = cached_entry.get("result")
            logger.debug(
                f"Branch cache HIT for function '{function_name}' on question '{question_name}'. "
                f"Using cached result: {cached_result!r}"
            )
            
            # Still need to apply operator if present
            if "op" in condition:
                expected_value = condition.get("value")
                operator = condition.get("op")
                try:
                    evaluation_result = ConditionOperator.evaluate(operator, cached_result, expected_value)
                    logger.debug(
                        f"Function '{function_name}' (cached) returned {cached_result!r}, operator '{operator}' "
                        f"evaluation: {evaluation_result}"
                    )
                    return evaluation_result
                except ValueError as e:
                    logger.warning(
                        f"Invalid operator '{operator}' in function condition for '{function_name}': {e}"
                    )
                    return False
            else:
                bool_result = bool(cached_result)
                logger.debug(
                    f"Function '{function_name}' (cached) returned {cached_result!r} (bool: {bool_result}) "
                    f"for question '{question_name}'"
                )
                return bool_result

        try:
            # Execute function (not in cache, or cache was invalid)
            if inspect.iscoroutinefunction(func):
                result = await func(session, visitor)
            else:
                result = func(session, visitor)
            
            # Extract accessed keys from instrumented responses (set by wrapper)
            accessed_keys = set()
            if hasattr(session, '_branch_function_accessed_keys'):
                accessed_keys = session._branch_function_accessed_keys
                # Clear for next function call
                del session._branch_function_accessed_keys
            
            # Cache the result with dependencies
            branch_cache.set(cache_key, result, accessed_keys)
            logger.debug(
                f"Branch cache SET for function '{function_name}' on question '{question_name}'. "
                f"Result: {result!r}, Dependencies: {accessed_keys}"
            )

            # Hybrid logic: check for operator
            if "op" in condition:
                # Function returns value, evaluate with operator
                expected_value = condition.get("value")
                operator = condition.get("op")
                try:
                    evaluation_result = ConditionOperator.evaluate(operator, result, expected_value)
                    logger.debug(
                        f"Function '{function_name}' returned {result!r}, operator '{operator}' "
                        f"evaluation: {evaluation_result}"
                    )
                    return evaluation_result
                except ValueError as e:
                    logger.warning(
                        f"Invalid operator '{operator}' in function condition for '{function_name}': {e}"
                    )
                    return False
            else:
                # Function returns boolean directly
                if not isinstance(result, bool):
                    logger.warning(
                        f"Branch function '{function_name}' returned {type(result).__name__} "
                        f"but bool expected when no operator is specified. Converting to bool."
                    )
                bool_result = bool(result)
                logger.debug(
                    f"Function '{function_name}' returned {result!r} (bool: {bool_result}) "
                    f"for question '{question_name}'"
                )
                return bool_result

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

        # Use branch cache to avoid repeated evaluation for the same operator+question
        from ..utils.cache_utils import BranchFunctionCache
        branch_cache = BranchFunctionCache(session)
        cache_key = branch_cache._make_cache_key(question_name, condition)
        cached_entry = branch_cache.get(cache_key)
        if cached_entry is not None:
            cached_result = cached_entry.get("result")
            logger.debug(f"Branch cache HIT (operator) for question '{question_name}': {cached_result!r}")
            return bool(cached_result) if operator not in ("exists", "is_set", "not_exists", "is_not_set") else ConditionOperator.evaluate(operator, cached_entry.get('result'))

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
            # Cache operator result with dependency on the implicit question
            try:
                branch_cache.set(cache_key, result, {question_name})
            except Exception:
                logger.debug("Failed to set branch cache for operator condition; continuing without cache")
            return result
        except ValueError as e:
            logger.warning(f"Invalid operator '{operator}' in condition for question '{question_name}': {e}")
            return False
