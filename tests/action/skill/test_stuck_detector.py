"""Tests for StuckDetector: stuck detection, correction limits, reset."""

import pytest

from jvagent.action.skill.stuck_detector import StuckDetector, StuckDetectorConfig


def _make_detector(window_size=3, max_corrections=2):
    return StuckDetector(
        StuckDetectorConfig(
            window_size=window_size,
            max_corrections=max_corrections,
        )
    )


def _tool_call(name="search", args="{}"):
    return [{"function": {"name": name, "arguments": args}}]


class TestRecord:
    def test_not_stuck_with_varied_calls(self):
        detector = _make_detector(window_size=3)
        detector.record(_tool_call("search"))
        detector.record(_tool_call("read"))
        detector.record(_tool_call("write"))
        assert detector.corrections == 0

    def test_detects_stuck_when_window_fills_with_same_signature(self):
        detector = _make_detector(window_size=3)
        # Same tool call 3 times
        result = None
        for _ in range(3):
            result = detector.record(_tool_call("search", '{"q": "test"}'))
        assert result is not None
        assert "repeat" in result.lower() or result != "FORCE_TERMINATE"
        assert detector.corrections == 1

    def test_force_terminate_after_max_corrections(self):
        detector = _make_detector(window_size=2, max_corrections=1)
        # First stuck: correction
        detector.record(_tool_call("search"))
        result = detector.record(_tool_call("search"))
        assert result is not None
        assert result != "FORCE_TERMINATE"
        # Second stuck: force terminate
        detector.record(_tool_call("search"))
        result = detector.record(_tool_call("search"))
        assert result == "FORCE_TERMINATE"

    def test_returns_none_when_not_stuck(self):
        detector = _make_detector(window_size=3)
        result = detector.record(_tool_call("search"))
        assert result is None
        result = detector.record(_tool_call("read"))
        assert result is None

    def test_window_resets_after_correction(self):
        detector = _make_detector(window_size=2, max_corrections=2)
        # Get stuck once
        detector.record(_tool_call("search"))
        result = detector.record(_tool_call("search"))
        assert result is not None  # correction issued, window cleared
        # Different call after correction should not trigger
        result = detector.record(_tool_call("read"))
        assert result is None

    def test_different_args_different_signature(self):
        detector = _make_detector(window_size=3)
        detector.record(_tool_call("search", '{"q": "a"}'))
        result = detector.record(_tool_call("search", '{"q": "b"}'))
        assert result is None

    def test_no_tool_calls_not_stuck(self):
        detector = _make_detector(window_size=3)
        result = detector.record([])
        assert result is None


class TestReset:
    def test_reset_clears_corrections(self):
        detector = _make_detector(window_size=2, max_corrections=5)
        detector.record(_tool_call("search"))
        detector.record(_tool_call("search"))
        assert detector.corrections == 1
        detector.reset()
        assert detector.corrections == 0

    def test_reset_clears_window(self):
        detector = _make_detector(window_size=2)
        detector.record(_tool_call("search"))
        detector.reset()
        # Should not detect stuck after reset with same call
        result = detector.record(_tool_call("search"))
        assert result is None  # window was cleared, only 1 entry


class TestBuildSignature:
    def test_deterministic_for_same_input(self):
        calls = _tool_call("search", '{"q": "test"}')
        sig1 = StuckDetector._build_signature(calls)
        sig2 = StuckDetector._build_signature(calls)
        assert sig1 == sig2

    def test_different_for_different_tools(self):
        sig1 = StuckDetector._build_signature(_tool_call("search"))
        sig2 = StuckDetector._build_signature(_tool_call("read"))
        assert sig1 != sig2

    def test_handles_missing_function(self):
        calls = [{}]
        sig = StuckDetector._build_signature(calls)
        assert "unknown" in sig

    def test_handles_empty_list(self):
        sig = StuckDetector._build_signature([])
        assert sig == ""


class TestCorrectionsProperty:
    def test_initial_zero(self):
        detector = _make_detector()
        assert detector.corrections == 0

    def test_increments_on_stuck(self):
        detector = _make_detector(window_size=2)
        detector.record(_tool_call("x"))
        detector.record(_tool_call("x"))
        assert detector.corrections == 1
