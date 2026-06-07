"""Branch condition evaluation for interview question paths."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, Optional

from ..session import InterviewSession

logger = logging.getLogger(__name__)


class ConditionOperator:
    """Evaluates condition operators for question branching."""

    @staticmethod
    def evaluate(operator: str, actual_value: Any, expected_value: Any = None) -> bool:
        operator = operator.lower().strip()
        if operator in ("equals", "=="):
            return ConditionOperator._equals(actual_value, expected_value)
        if operator in ("!=", "not_equals"):
            return not ConditionOperator._equals(actual_value, expected_value)
        if operator in (">", "greater_than"):
            return ConditionOperator._compare(
                actual_value, expected_value, lambda a, e: a > e
            )
        if operator in (">=", "greater_than_or_equal"):
            return ConditionOperator._compare(
                actual_value, expected_value, lambda a, e: a >= e
            )
        if operator in ("<", "less_than"):
            return ConditionOperator._compare(
                actual_value, expected_value, lambda a, e: a < e
            )
        if operator in ("<=", "less_than_or_equal"):
            return ConditionOperator._compare(
                actual_value, expected_value, lambda a, e: a <= e
            )
        if operator in ("in", "in_list"):
            return ConditionOperator._in(actual_value, expected_value)
        if operator in ("not_in", "not_in_list"):
            return not ConditionOperator._in(actual_value, expected_value)
        if operator == "contains":
            return ConditionOperator._contains(actual_value, expected_value)
        if operator == "not_contains":
            return not ConditionOperator._contains(actual_value, expected_value)
        if operator in ("exists", "is_set"):
            return ConditionOperator._exists(actual_value)
        if operator in ("not_exists", "is_not_set"):
            return not ConditionOperator._exists(actual_value)
        raise ValueError(f"Unknown condition operator: {operator}")

    @staticmethod
    def _normalize(value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @staticmethod
    def _coerce_number(value: Any) -> Optional[float]:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value) if "." in value else float(int(value))
            except ValueError:
                return None
        return None

    @staticmethod
    def _equals(actual: Any, expected: Any) -> bool:
        if actual is None or expected is None:
            return actual == expected
        return ConditionOperator._normalize(actual) == ConditionOperator._normalize(
            expected
        )

    @staticmethod
    def _compare(actual: Any, expected: Any, op) -> bool:
        a = ConditionOperator._coerce_number(actual)
        e = ConditionOperator._coerce_number(expected)
        if a is None or e is None:
            return False
        return op(a, e)

    @staticmethod
    def _in(actual: Any, expected: Any) -> bool:
        if not isinstance(expected, (list, tuple, set)):
            return False
        norm = ConditionOperator._normalize(actual)
        return norm in [ConditionOperator._normalize(v) for v in expected]

    @staticmethod
    def _contains(actual: Any, expected: Any) -> bool:
        if isinstance(actual, str):
            return str(expected).lower() in actual.lower()
        if isinstance(actual, (list, tuple, set)):
            return ConditionOperator._normalize(expected) in [
                ConditionOperator._normalize(v) for v in actual
            ]
        return False

    @staticmethod
    def _exists(actual: Any) -> bool:
        if actual is None:
            return False
        if isinstance(actual, str):
            return bool(actual.strip())
        return True


async def matches_branch_condition(
    condition: Dict[str, Any],
    session: InterviewSession,
    implicit_question: str,
    load_function: Callable[[str], Optional[Callable]],
    visitor: Any = None,
    interview_action: Any = None,
) -> bool:
    """Return True when a branch condition matches the current session state."""
    if not condition or not implicit_question:
        return False

    if "function" in condition:
        function_name = condition.get("function")
        if not function_name:
            return False
        operator = condition.get("op")
        is_existence = operator in ("exists", "is_set", "not_exists", "is_not_set")
        if not is_existence and implicit_question not in session.fields:
            return False
        func = load_function(function_name)
        if not func:
            logger.error("Branch function '%s' not found", function_name)
            return False
        result = func(
            **{
                k: v
                for k, v in {
                    "session": session,
                    "visitor": visitor,
                    "interview_action": interview_action,
                }.items()
                if k in _sig_params(func)
            }
        )
        if asyncio.iscoroutine(result):
            result = await result
        if operator:
            try:
                return ConditionOperator.evaluate(
                    operator, result, condition.get("value")
                )
            except ValueError:
                return False
        return bool(result)

    actual = session.get_value(implicit_question)
    op = condition.get("op", "equals")
    try:
        return ConditionOperator.evaluate(op, actual, condition.get("value"))
    except ValueError:
        return False


def _sig_params(func: Callable) -> set:
    import inspect

    try:
        return set(inspect.signature(func).parameters.keys())
    except (ValueError, TypeError):
        return set()
