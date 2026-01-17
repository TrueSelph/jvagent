"""Condition operators for interview question branching.

This module provides operators for evaluating conditions in question graph branches.
Supports equality, comparison, membership, existence, and pattern matching operations.
"""

import re
from typing import Any, Dict, List, Optional, Union

logger = None  # Will be set when needed


class ConditionOperator:
    """Evaluates condition operators for question branching."""
    
    @staticmethod
    def evaluate(
        operator: str,
        actual_value: Any,
        expected_value: Any = None
    ) -> bool:
        """Evaluate a condition operator.
        
        Args:
            operator: Operator name (e.g., "equals", ">=", "in", "exists")
            actual_value: The actual value from session responses
            expected_value: The expected value for comparison (not needed for existence operators)
            
        Returns:
            True if condition matches, False otherwise
            
        Raises:
            ValueError: If operator is not recognized
        """
        # Normalize operator name
        operator = operator.lower().strip()
        
        # Handle equality operators
        if operator == "equals" or operator == "==":
            return ConditionOperator._equals(actual_value, expected_value)
        elif operator == "!=" or operator == "not_equals":
            return ConditionOperator._not_equals(actual_value, expected_value)
        elif operator == ">" or operator == "greater_than":
            return ConditionOperator._greater_than(actual_value, expected_value)
        elif operator == ">=" or operator == "greater_than_or_equal":
            return ConditionOperator._greater_than_or_equal(actual_value, expected_value)
        elif operator == "<" or operator == "less_than":
            return ConditionOperator._less_than(actual_value, expected_value)
        elif operator == "<=" or operator == "less_than_or_equal":
            return ConditionOperator._less_than_or_equal(actual_value, expected_value)
        elif operator == "in" or operator == "in_list":
            return ConditionOperator._in(actual_value, expected_value)
        elif operator == "not_in" or operator == "not_in_list":
            return ConditionOperator._not_in(actual_value, expected_value)
        elif operator == "contains":
            return ConditionOperator._contains(actual_value, expected_value)
        elif operator == "not_contains":
            return ConditionOperator._not_contains(actual_value, expected_value)
        elif operator == "exists" or operator == "is_set":
            return ConditionOperator._exists(actual_value)
        elif operator == "not_exists" or operator == "is_not_set":
            return ConditionOperator._not_exists(actual_value)
        elif operator == "matches" or operator == "regex":
            return ConditionOperator._matches(actual_value, expected_value)
        elif operator == "not_matches" or operator == "not_regex":
            return ConditionOperator._not_matches(actual_value, expected_value)
        else:
            raise ValueError(f"Unknown condition operator: {operator}")
    
    @staticmethod
    def _normalize_value(value: Any) -> Any:
        """Normalize value for comparison (case-insensitive strings, type coercion)."""
        if isinstance(value, str):
            return value.strip().lower()
        return value
    
    @staticmethod
    def _coerce_to_number(value: Any) -> Optional[Union[int, float]]:
        """Coerce value to number for numeric comparisons."""
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            try:
                # Try int first
                if '.' not in value:
                    return int(value)
                return float(value)
            except ValueError:
                return None
        return None
    
    @staticmethod
    def _equals(actual: Any, expected: Any) -> bool:
        """Check if values are equal (case-insensitive for strings)."""
        if actual is None or expected is None:
            return actual == expected
        normalized_actual = ConditionOperator._normalize_value(actual)
        normalized_expected = ConditionOperator._normalize_value(expected)
        return normalized_actual == normalized_expected
    
    @staticmethod
    def _not_equals(actual: Any, expected: Any) -> bool:
        """Check if values are not equal."""
        return not ConditionOperator._equals(actual, expected)
    
    @staticmethod
    def _greater_than(actual: Any, expected: Any) -> bool:
        """Check if actual > expected (numeric comparison)."""
        actual_num = ConditionOperator._coerce_to_number(actual)
        expected_num = ConditionOperator._coerce_to_number(expected)
        if actual_num is None or expected_num is None:
            return False
        return actual_num > expected_num
    
    @staticmethod
    def _greater_than_or_equal(actual: Any, expected: Any) -> bool:
        """Check if actual >= expected (numeric comparison)."""
        actual_num = ConditionOperator._coerce_to_number(actual)
        expected_num = ConditionOperator._coerce_to_number(expected)
        if actual_num is None or expected_num is None:
            return False
        return actual_num >= expected_num
    
    @staticmethod
    def _less_than(actual: Any, expected: Any) -> bool:
        """Check if actual < expected (numeric comparison)."""
        actual_num = ConditionOperator._coerce_to_number(actual)
        expected_num = ConditionOperator._coerce_to_number(expected)
        if actual_num is None or expected_num is None:
            return False
        return actual_num < expected_num
    
    @staticmethod
    def _less_than_or_equal(actual: Any, expected: Any) -> bool:
        """Check if actual <= expected (numeric comparison)."""
        actual_num = ConditionOperator._coerce_to_number(actual)
        expected_num = ConditionOperator._coerce_to_number(expected)
        if actual_num is None or expected_num is None:
            return False
        return actual_num <= expected_num
    
    @staticmethod
    def _in(actual: Any, expected: Any) -> bool:
        """Check if actual value is in expected list."""
        if not isinstance(expected, (list, tuple, set)):
            return False
        normalized_actual = ConditionOperator._normalize_value(actual)
        normalized_expected = [ConditionOperator._normalize_value(v) for v in expected]
        return normalized_actual in normalized_expected
    
    @staticmethod
    def _not_in(actual: Any, expected: Any) -> bool:
        """Check if actual value is not in expected list."""
        return not ConditionOperator._in(actual, expected)
    
    @staticmethod
    def _contains(actual: Any, expected: Any) -> bool:
        """Check if actual value (string or list) contains expected value."""
        if isinstance(actual, str):
            normalized_actual = actual.lower()
            normalized_expected = str(expected).lower()
            return normalized_expected in normalized_actual
        elif isinstance(actual, (list, tuple, set)):
            normalized_expected = ConditionOperator._normalize_value(expected)
            normalized_actual = [ConditionOperator._normalize_value(v) for v in actual]
            return normalized_expected in normalized_actual
        return False
    
    @staticmethod
    def _not_contains(actual: Any, expected: Any) -> bool:
        """Check if actual value does not contain expected value."""
        return not ConditionOperator._contains(actual, expected)
    
    @staticmethod
    def _exists(actual: Any) -> bool:
        """Check if value exists (is not None and not empty string)."""
        if actual is None:
            return False
        if isinstance(actual, str):
            return bool(actual.strip())
        return True
    
    @staticmethod
    def _not_exists(actual: Any) -> bool:
        """Check if value does not exist (is None or empty)."""
        return not ConditionOperator._exists(actual)
    
    @staticmethod
    def _matches(actual: Any, expected: Any) -> bool:
        """Check if actual value matches regex pattern."""
        if not isinstance(actual, str) or not isinstance(expected, str):
            return False
        try:
            return bool(re.search(expected, actual, re.IGNORECASE))
        except re.error:
            return False
    
    @staticmethod
    def _not_matches(actual: Any, expected: Any) -> bool:
        """Check if actual value does not match regex pattern."""
        return not ConditionOperator._matches(actual, expected)
    
    @staticmethod
    def get_supported_operators() -> List[str]:
        """Get list of supported operator names.
        
        Returns:
            List of operator names
        """
        return [
            "equals", "==", "!=", "not_equals",
            ">", "greater_than", ">=", "greater_than_or_equal",
            "<", "less_than", "<=", "less_than_or_equal",
            "in", "in_list", "not_in", "not_in_list",
            "contains", "not_contains",
            "exists", "is_set", "not_exists", "is_not_set",
            "matches", "regex", "not_matches", "not_regex",
        ]
    
    @staticmethod
    def validate_operator(operator: str) -> bool:
        """Validate that an operator is supported.
        
        Args:
            operator: Operator name to validate
            
        Returns:
            True if operator is supported, False otherwise
        """
        try:
            ConditionOperator.evaluate(operator, None, None)
            return True
        except ValueError:
            return False
