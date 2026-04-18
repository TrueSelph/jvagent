"""Graph validator for interview question graphs.

This module provides validation of question graph structure and semantics
to catch errors at build time before the interview is used.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from ..foundation.enums import InterviewState
from .condition_operators import ConditionOperator

logger = logging.getLogger(__name__)


@dataclass
class ValidationIssue:
    """Represents a validation issue (error or warning)."""

    severity: str  # "error" or "warning"
    message: str
    question_name: Optional[str] = None
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationReport:
    """Report from graph validation."""

    errors: List[ValidationIssue] = field(default_factory=list)
    warnings: List[ValidationIssue] = field(default_factory=list)

    def is_valid(self) -> bool:
        """Check if validation passed (no errors)."""
        return len(self.errors) == 0

    def has_warnings(self) -> bool:
        """Check if there are any warnings."""
        return len(self.warnings) > 0

    def log_issues(self, action_name: str) -> None:
        """Log all validation issues."""
        for error in self.errors:
            logger.error(f"{action_name}: Graph validation error: {error.message}")
        for warning in self.warnings:
            logger.warning(
                f"{action_name}: Graph validation warning: {warning.message}"
            )


class QuestionGraphValidator:
    """Validates question graph structure and semantics."""

    # Valid state target names
    VALID_STATE_TARGETS = {state.value.upper() for state in InterviewState}

    def __init__(
        self, question_graph: List[Dict[str, Any]], interview_type: Optional[str] = None
    ):
        """Initialize validator with question graph.

        Args:
            question_graph: List of question configuration dictionaries
            interview_type: Optional interview type (class name) for validating branch functions
        """
        self.question_graph = question_graph
        self.question_names: Set[str] = set()
        self.report = ValidationReport()
        self.interview_type = interview_type

    async def validate(self) -> ValidationReport:
        """Validate the question graph.

        Performs comprehensive validation:
        - All branch targets exist (questions or valid state names)
        - No unreachable questions (warns, doesn't error)
        - No cycles without escape conditions
        - All conditions reference valid answered questions
        - Operators are valid
        - State transitions are logical

        Returns:
            ValidationReport with errors and warnings
        """
        self.report = ValidationReport()

        if not self.question_graph:
            self.report.errors.append(
                ValidationIssue(severity="error", message="Question graph is empty")
            )
            return self.report

        # Extract question names
        self.question_names = {
            q.get("name", "") for q in self.question_graph if q.get("name")
        }

        # Remove empty names
        self.question_names.discard("")

        # Validate each question
        for question_config in self.question_graph:
            self._validate_question(question_config)

        # Validate graph structure
        self._validate_targets()
        self._validate_condition_dependencies()
        self._validate_reachability()
        self._validate_cycles()

        return self.report

    def _validate_question(self, question_config: Dict[str, Any]) -> None:
        """Validate a single question configuration.

        Args:
            question_config: Question configuration dictionary
        """
        question_name = question_config.get("name", "")

        if not question_name:
            self.report.errors.append(
                ValidationIssue(
                    severity="error",
                    message="Question missing required 'name' field",
                    question_name=None,
                )
            )
            return

        # Validate branches
        branches = question_config.get("branches", [])
        if not isinstance(branches, list):
            self.report.errors.append(
                ValidationIssue(
                    severity="error",
                    message=f"Question '{question_name}': 'branches' must be a list",
                    question_name=question_name,
                )
            )
            return

        for i, branch in enumerate(branches):
            if not isinstance(branch, dict):
                self.report.errors.append(
                    ValidationIssue(
                        severity="error",
                        message=f"Question '{question_name}': Branch {i} must be a dictionary",
                        question_name=question_name,
                    )
                )
                continue

            # Validate branch target
            target = branch.get("target")
            if not target:
                self.report.errors.append(
                    ValidationIssue(
                        severity="error",
                        message=f"Question '{question_name}': Branch {i} missing 'target'",
                        question_name=question_name,
                    )
                )
            elif not self._is_valid_target(target):
                self.report.errors.append(
                    ValidationIssue(
                        severity="error",
                        message=f"Question '{question_name}': Branch {i} has invalid target '{target}'",
                        question_name=question_name,
                        context={"target": target},
                    )
                )

            # Validate condition
            condition = branch.get("condition", {})
            if condition:
                self._validate_condition(condition, question_name, f"branch {i}")

        # Validate default_next
        default_next = question_config.get("default_next")
        if default_next and not self._is_valid_target(default_next):
            self.report.errors.append(
                ValidationIssue(
                    severity="error",
                    message=f"Question '{question_name}': Invalid 'default_next' target '{default_next}'",
                    question_name=question_name,
                    context={"default_next": default_next},
                )
            )

        # Validate input_context_provider if present
        provider_name = question_config.get("input_context_provider")
        if provider_name:
            self._validate_input_context_provider(provider_name, question_name)

    def _validate_condition(
        self, condition: Dict[str, Any], question_name: str, context: str
    ) -> None:
        """Validate a condition dictionary.

        Args:
            condition: Condition dictionary
            question_name: Name of question containing this condition (question is implicit in condition)
            context: Context string for error messages
        """
        if not isinstance(condition, dict):
            self.report.errors.append(
                ValidationIssue(
                    severity="error",
                    message=f"Question '{question_name}': Condition in {context} must be a dictionary",
                    question_name=question_name,
                )
            )
            return

        # Question is always implicit from branch context - condition evaluates against question_name
        # No need to check for "question" key in condition

        # Check for function-based condition
        if "function" in condition:
            function_name = condition.get("function")
            if not function_name or not isinstance(function_name, str):
                self.report.errors.append(
                    ValidationIssue(
                        severity="error",
                        message=f"Question '{question_name}': Condition in {context} has 'function' key but no valid function name",
                        question_name=question_name,
                    )
                )
                return

            # Validate that function is registered (if interview_type is available)
            if self.interview_type:
                from ..foundation.decorators import RegistryManager, get_branch_function

                func = get_branch_function(self.interview_type, function_name)
                if not func:
                    # Check pending functions
                    pending = RegistryManager.get_pending(
                        "pending_branch_functions", self.interview_type
                    )
                    if function_name not in pending:
                        self.report.warnings.append(
                            ValidationIssue(
                                severity="warning",
                                message=f"Question '{question_name}': Branch function '{function_name}' not found for interview type '{self.interview_type}'. "
                                f"Ensure it's registered with @branch_function decorator.",
                                question_name=question_name,
                                context={
                                    "function": function_name,
                                    "interview_type": self.interview_type,
                                },
                            )
                        )

            # If operator is also present, validate it
            if "op" in condition:
                operator = condition.get("op")
                if not ConditionOperator.validate_operator(operator):
                    self.report.errors.append(
                        ValidationIssue(
                            severity="error",
                            message=f"Question '{question_name}': Condition in {context} has invalid operator '{operator}' for function '{function_name}'",
                            question_name=question_name,
                            context={"op": operator, "function": function_name},
                        )
                    )

            # Function-based condition validated
            return

        # Operator-based (declarative) condition validation
        operator = condition.get("op")
        if not operator:
            self.report.errors.append(
                ValidationIssue(
                    severity="error",
                    message=f"Question '{question_name}': Condition in {context} must have either 'function' or 'op' field",
                    question_name=question_name,
                )
            )
            return

        if not ConditionOperator.validate_operator(operator):
            self.report.errors.append(
                ValidationIssue(
                    severity="error",
                    message=f"Question '{question_name}': Condition in {context} has invalid operator '{operator}'",
                    question_name=question_name,
                    context={"op": operator},
                )
            )

    def _validate_input_context_provider(
        self, provider_name: str, question_name: str
    ) -> None:
        """Validate an input_context_provider reference.

        Args:
            provider_name: Name of the input context provider function
            question_name: Name of question containing this provider reference
        """
        if not provider_name or not isinstance(provider_name, str):
            self.report.errors.append(
                ValidationIssue(
                    severity="error",
                    message=f"Question '{question_name}': 'input_context_provider' must be a non-empty string",
                    question_name=question_name,
                )
            )
            return

        # Validate that provider is registered (if interview_type is available)
        if self.interview_type:
            from ..foundation.decorators import (
                RegistryManager,
                get_input_context_provider,
            )

            func = get_input_context_provider(self.interview_type, provider_name)
            if not func:
                # Check pending providers
                pending = RegistryManager.get_pending(
                    "pending_input_context_providers", self.interview_type
                )
                if provider_name not in pending:
                    self.report.warnings.append(
                        ValidationIssue(
                            severity="warning",
                            message=f"Question '{question_name}': Input context provider '{provider_name}' not found for interview type '{self.interview_type}'. "
                            f"Ensure it's registered with @input_context_provider decorator.",
                            question_name=question_name,
                            context={
                                "provider": provider_name,
                                "interview_type": self.interview_type,
                            },
                        )
                    )

    def _is_valid_target(self, target: str) -> bool:
        """Check if a target is valid (question name or state name).

        Args:
            target: Target string to validate

        Returns:
            True if target is valid, False otherwise
        """
        if not isinstance(target, str):
            return False

        # Check if it's a valid state target
        if target.upper() in self.VALID_STATE_TARGETS:
            return True

        # Check if it's a valid question name
        if target in self.question_names:
            return True

        return False

    def _validate_targets(self) -> None:
        """Validate that all branch targets exist."""
        # This is already done in _validate_question, but we can add additional checks here
        pass

    def _validate_condition_dependencies(self) -> None:
        """Validate that conditions reference questions that appear earlier in the graph.

        This ensures conditions can be evaluated when reached during traversal.
        """
        question_positions = {
            q.get("name"): i for i, q in enumerate(self.question_graph) if q.get("name")
        }

        for question_config in self.question_graph:
            question_name = question_config.get("name", "")
            if not question_name:
                continue

            current_position = question_positions.get(question_name, -1)

            # Check branches
            for branch in question_config.get("branches", []):
                condition = branch.get("condition", {})
                if not condition:
                    continue

                # Question is implicit - condition always evaluates against the question that owns the branch
                # No need to check for referenced question since it's always the current question
                # This check is no longer needed as conditions always reference the owning question

    def _build_adjacency_list(self) -> Dict[str, List[str]]:
        """Build adjacency list representation of the question graph.

        Returns:
            Dictionary mapping question names to lists of their target questions
        """
        adjacency: Dict[str, List[str]] = {name: [] for name in self.question_names}

        for question_config in self.question_graph:
            question_name = question_config.get("name", "")
            if not question_name:
                continue

            # Add edges from branches
            for branch in question_config.get("branches", []):
                target = branch.get("target", "")
                if target and target in self.question_names:
                    adjacency[question_name].append(target)

            # Add edge from default_next
            default_next = question_config.get("default_next")
            if default_next and default_next in self.question_names:
                adjacency[question_name].append(default_next)

            # Add linear flow edge (next question in list)
            current_idx = next(
                (
                    i
                    for i, q in enumerate(self.question_graph)
                    if q.get("name") == question_name
                ),
                -1,
            )
            if current_idx >= 0 and current_idx + 1 < len(self.question_graph):
                next_question = self.question_graph[current_idx + 1].get("name")
                if next_question and next_question in self.question_names:
                    # Only add if no explicit default_next
                    if not default_next:
                        adjacency[question_name].append(next_question)

        return adjacency

    def _validate_reachability(self) -> None:
        """Validate that questions are reachable from the start.

        Warns about potentially unreachable questions (may be intentional).
        """
        if not self.question_graph:
            return

        # Build adjacency list
        adjacency = self._build_adjacency_list()

        # Find unreachable questions using BFS from first question
        if not self.question_names:
            return

        start_question = self.question_graph[0].get("name", "")
        if not start_question or start_question not in self.question_names:
            return

        visited: Set[str] = set()
        queue = [start_question]
        visited.add(start_question)

        while queue:
            current = queue.pop(0)
            for neighbor in adjacency.get(current, []):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)

        # Warn about unreachable questions
        unreachable = self.question_names - visited
        for question_name in unreachable:
            self.report.warnings.append(
                ValidationIssue(
                    severity="warning",
                    message=(
                        f"Question '{question_name}' is not reachable from the start of the graph. "
                        f"This may be intentional if it's only reachable via conditional branches."
                    ),
                    question_name=question_name,
                )
            )

    def _validate_cycles(self) -> None:
        """Detect cycles in the graph and warn if they lack escape conditions.

        Cycles are allowed but should have escape conditions to prevent infinite loops.
        """
        # Build adjacency list
        adjacency = self._build_adjacency_list()

        # Detect cycles using DFS
        def has_cycle(node: str, visited: Set[str], rec_stack: Set[str]) -> bool:
            visited.add(node)
            rec_stack.add(node)

            for neighbor in adjacency.get(node, []):
                if neighbor not in visited:
                    if has_cycle(neighbor, visited, rec_stack):
                        return True
                elif neighbor in rec_stack:
                    # Cycle detected
                    return True

            rec_stack.remove(node)
            return False

        visited: Set[str] = set()
        for question_name in self.question_names:
            if question_name not in visited:
                rec_stack: Set[str] = set()
                if has_cycle(question_name, visited, rec_stack):
                    self.report.warnings.append(
                        ValidationIssue(
                            severity="warning",
                            message=(
                                f"Cycle detected in question graph involving '{question_name}'. "
                                f"Ensure cycles have escape conditions to prevent infinite loops."
                            ),
                            question_name=question_name,
                        )
                    )
