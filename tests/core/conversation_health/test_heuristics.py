"""Tests for conversation_health.heuristics module."""

from types import SimpleNamespace

import pytest

from jvagent.core.conversation_health.heuristics import (
    detect_empty_or_trivial,
    detect_idk,
    run_heuristics,
)


class TestDetectIdk:
    """Tests for detect_idk function."""

    def test_idk_pattern_detected(self):
        """Detects common IDK patterns in response."""
        utterance = "What is the capital of France?"
        response = "I don't know the answer to that."
        result = detect_idk(utterance, response)
        assert result is not None
        assert result["code"] == "idk_response"
        assert result["dimension"] == "quality"
        assert result["severity"] == "high"

    def test_idk_not_question(self):
        """Short non-question utterance → no issue."""
        utterance = "hi"
        response = "I'm not sure about that."
        result = detect_idk(utterance, response)
        assert result is None

    def test_no_idk_in_response(self):
        """No IDK pattern in response → no issue."""
        utterance = "What is the capital of France?"
        response = "The capital of France is Paris."
        result = detect_idk(utterance, response)
        assert result is None

    def test_idk_with_question_mark(self):
        """Question mark in utterance triggers check."""
        utterance = "Can you help?"
        response = "I cannot help with that."
        result = detect_idk(utterance, response)
        assert result is not None

    def test_idk_long_utterance_no_question_mark(self):
        """Long utterance without question mark also checked."""
        utterance = "Please tell me about quantum physics and its applications"
        response = "I don't have information on that topic."
        result = detect_idk(utterance, response)
        assert result is not None


class TestDetectEmptyOrTrivial:
    """Tests for detect_empty_or_trivial function."""

    def test_empty_response(self):
        """Empty string → issue."""
        result = detect_empty_or_trivial("")
        assert result is not None
        assert result["code"] == "empty_or_trivial_response"
        assert result["dimension"] == "quality"

    def test_whitespace_only(self):
        """Whitespace-only → issue."""
        result = detect_empty_or_trivial("   \n  ")
        assert result is not None

    def test_very_short_response(self):
        """< 3 chars → issue."""
        result = detect_empty_or_trivial("ok")
        assert result is not None

    def test_trivial_reply(self):
        """Trivial replies like 'yes', 'no' → issue."""
        for reply in ["yes", "no", "okay", "thanks", "hi"]:
            result = detect_empty_or_trivial(reply)
            assert result is not None, f"Expected issue for '{reply}'"

    def test_substantial_response(self):
        """Substantial response → no issue."""
        response = "That's an interesting question! Let me explain."
        result = detect_empty_or_trivial(response)
        assert result is None


class TestRunHeuristics:
    """Tests for run_heuristics function."""

    def test_no_issues(self):
        """Healthy interaction → empty issue list."""
        result = run_heuristics(
            utterance="What is 2+2?",
            response="The answer is 4.",
            duration=1.0,
        )
        assert result == []

    def test_multiple_issues_detected(self):
        """Multiple heuristics can fire."""
        result = run_heuristics(
            utterance="What is the answer?",
            response="",
            duration=20.0,
        )
        # Should detect slow_response + empty_or_trivial + unanswered_question
        assert len(result) >= 2
        codes = [issue["code"] for issue in result]
        assert "empty_or_trivial_response" in codes
        assert "slow_response" in codes

    def test_prior_agent_responses(self):
        """Pass prior responses to detect repetition."""
        result = run_heuristics(
            utterance="Tell me something.",
            response="This is my answer.",
            prior_agent_responses=["Different answer."],
        )
        # No repetition
        assert not any(issue["code"] == "repetition_loop" for issue in result)

    def test_interaction_object(self):
        """Interaction object passed to detect_execution_failure."""
        fake_interaction = SimpleNamespace(
            events=[{"content": "Error occurred"}],
            response="Oops",
        )
        result = run_heuristics(
            utterance="Do something.",
            response="Oops",
            interaction=fake_interaction,
        )
        # execution_failure should be detected
        codes = [issue["code"] for issue in result]
        assert "execution_failure" in codes

    def test_custom_latency_bands(self):
        """Custom latency bands apply."""
        result = run_heuristics(
            utterance="Hi",
            response="Hello!",
            duration=2.5,
            latency_bands=[(2.0, "high")],
        )
        assert len(result) == 1
        assert result[0]["code"] == "slow_response"
        assert result[0]["severity"] == "high"

    def test_excerpt_max(self):
        """excerpt_max parameter propagates to issues."""
        long_response = "x" * 200
        result = run_heuristics(
            utterance="What is the answer?",
            response=long_response,
            duration=None,
            excerpt_max=50,
        )
        # At least one issue should have evidence excerpt
        for issue in result:
            if "evidence" in issue and "excerpt" in issue["evidence"]:
                assert len(issue["evidence"]["excerpt"]) <= 51  # +1 for ellipsis
