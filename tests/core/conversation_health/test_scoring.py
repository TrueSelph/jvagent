"""Tests for conversation_health.scoring module."""

import pytest

from jvagent.core.conversation_health.scoring import (
    heuristic_health_score,
    is_flagged,
    score_dimensions,
)


class TestScoreDimensions:
    """Tests for score_dimensions function."""

    def test_empty_issues(self):
        """No issues → all dimensions at 100."""
        result = score_dimensions([])
        assert result == {
            "quality": 100,
            "responsiveness": 100,
            "friction": 100,
            "integrity": 100,
        }

    def test_single_issue_deduction(self):
        """Single issue deducts from relevant dimension."""
        issues = [{"dimension": "quality", "deduction": 30}]
        result = score_dimensions(issues)
        assert result["quality"] == 70
        assert result["responsiveness"] == 100
        assert result["friction"] == 100
        assert result["integrity"] == 100

    def test_multiple_deductions_same_dimension(self):
        """Multiple issues on same dimension accumulate."""
        issues = [
            {"dimension": "quality", "deduction": 20},
            {"dimension": "quality", "deduction": 10},
        ]
        result = score_dimensions(issues)
        assert result["quality"] == 70

    def test_floor_at_zero(self):
        """Score cannot go negative."""
        issues = [{"dimension": "quality", "deduction": 150}]
        result = score_dimensions(issues)
        assert result["quality"] == 0

    def test_unknown_dimension_ignored(self):
        """Issues with unknown dimensions are skipped."""
        issues = [
            {"dimension": "unknown_dim", "deduction": 50},
            {"dimension": "quality", "deduction": 10},
        ]
        result = score_dimensions(issues)
        assert result["quality"] == 90
        assert "unknown_dim" not in result

    def test_missing_or_invalid_deduction(self):
        """Missing or invalid deduction treated as 0."""
        issues = [
            {"dimension": "quality"},
            {"dimension": "responsiveness", "deduction": "not_a_number"},
            {"dimension": "friction", "deduction": None},
        ]
        result = score_dimensions(issues)
        assert result["quality"] == 100
        assert result["responsiveness"] == 100
        assert result["friction"] == 100

    def test_negative_deduction_ignored(self):
        """Negative deductions clamped to 0."""
        issues = [{"dimension": "quality", "deduction": -10}]
        result = score_dimensions(issues)
        assert result["quality"] == 100


class TestHeuristicHealthScore:
    """Tests for heuristic_health_score function."""

    def test_all_perfect(self):
        """All dimensions at 100 → score 100."""
        dimensions = {
            "quality": 100,
            "responsiveness": 100,
            "friction": 100,
            "integrity": 100,
        }
        result = heuristic_health_score(dimensions)
        assert result == 100.0

    def test_equal_weight_mean(self):
        """Score is mean of four dimensions."""
        dimensions = {
            "quality": 80,
            "responsiveness": 90,
            "friction": 70,
            "integrity": 60,
        }
        # (80 + 90 + 70 + 60) / 4 = 75
        result = heuristic_health_score(dimensions)
        assert result == 75.0

    def test_missing_dimension_defaults_to_100(self):
        """Missing dimensions default to 100."""
        dimensions = {"quality": 50}
        # (50 + 100 + 100 + 100) / 4 = 87.5
        result = heuristic_health_score(dimensions)
        assert result == 87.5

    def test_empty_dimensions(self):
        """Empty dict → 100.0."""
        result = heuristic_health_score({})
        assert result == 100.0

    def test_rounding(self):
        """Result rounded to 2 decimals."""
        dimensions = {
            "quality": 85,
            "responsiveness": 75,
            "friction": 68,
            "integrity": 92,
        }
        # (85 + 75 + 68 + 92) / 4 = 80.0
        result = heuristic_health_score(dimensions)
        assert result == 80.0


class TestIsFlagged:
    """Tests for is_flagged function."""

    def test_not_flagged_when_above_threshold(self):
        """All dimensions above threshold, no high-severity issues → not flagged."""
        dimensions = {
            "quality": 75,
            "responsiveness": 80,
            "friction": 90,
            "integrity": 85,
        }
        issues = [{"severity": "low"}]
        result = is_flagged(dimensions, issues, flag_threshold=70.0)
        assert result is False

    def test_flagged_when_dimension_below_threshold(self):
        """Any dimension below threshold → flagged."""
        dimensions = {
            "quality": 65,
            "responsiveness": 80,
            "friction": 90,
            "integrity": 85,
        }
        issues = []
        result = is_flagged(dimensions, issues, flag_threshold=70.0)
        assert result is True

    def test_flagged_when_high_severity_issue(self):
        """High-severity issue → flagged."""
        dimensions = {
            "quality": 75,
            "responsiveness": 80,
            "friction": 90,
            "integrity": 85,
        }
        issues = [{"severity": "high"}]
        result = is_flagged(dimensions, issues, flag_threshold=70.0)
        assert result is True

    def test_not_flagged_missing_dimensions(self):
        """Missing dimensions default to 100 (not flagged)."""
        dimensions = {}
        issues = []
        result = is_flagged(dimensions, issues, flag_threshold=70.0)
        assert result is False

    def test_flagged_multiple_high_severity_issues(self):
        """Multiple high-severity issues → flagged."""
        dimensions = {
            "quality": 75,
            "responsiveness": 80,
            "friction": 90,
            "integrity": 85,
        }
        issues = [{"severity": "medium"}, {"severity": "high"}]
        result = is_flagged(dimensions, issues, flag_threshold=70.0)
        assert result is True

    def test_severity_case_insensitive(self):
        """Severity check is case-insensitive."""
        dimensions = {
            "quality": 75,
            "responsiveness": 80,
            "friction": 90,
            "integrity": 85,
        }
        issues = [{"severity": "HIGH"}]
        result = is_flagged(dimensions, issues, flag_threshold=70.0)
        assert result is True

    def test_custom_threshold(self):
        """Custom threshold applies."""
        dimensions = {
            "quality": 85,
            "responsiveness": 80,
            "friction": 90,
            "integrity": 75,
        }
        issues = []
        result = is_flagged(dimensions, issues, flag_threshold=90.0)
        assert result is True
