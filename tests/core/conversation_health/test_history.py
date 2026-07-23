"""Unit tests for Conversation Health prior-only history helpers."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from jvagent.core.conversation_health.history import (
    AI_HISTORY_MAX_CHARS,
    history_for_ai,
    prior_interactions,
    prior_responses_for_heuristics,
)


def _turn(i: int, *, utterance: str | None = None, response: str | None = None):
    base = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=f"ix-{i}",
        started_at=base + timedelta(minutes=i),
        utterance=utterance if utterance is not None else f"user-{i}",
        response=response if response is not None else f"agent-{i}",
    )


class TestPriorInteractions:
    def test_slice_at_index_1_limit_default(self):
        turns = [_turn(i) for i in range(5)]
        priors = prior_interactions(turns, "ix-1", limit=6)
        assert [t.id for t in priors] == ["ix-0"]

    def test_slice_respects_limit_not_future(self):
        turns = [_turn(i) for i in range(5)]
        # Target index 3, limit 2 → turns 1 and 2 (not 3, not 4)
        priors = prior_interactions(turns, "ix-3", limit=2)
        assert [t.id for t in priors] == ["ix-1", "ix-2"]

    def test_missing_target_returns_empty(self):
        turns = [_turn(i) for i in range(3)]
        assert prior_interactions(turns, "missing", limit=6) == []

    def test_first_turn_has_no_priors(self):
        turns = [_turn(i) for i in range(3)]
        assert prior_interactions(turns, "ix-0", limit=6) == []

    def test_future_excluded_from_ai_history(self):
        """Regression: newest-window must not inject later turns into AI history."""
        turns = [
            _turn(0, utterance="hey", response="Hi!"),
            _turn(1, utterance="tell me a joke", response="Why did the scarecrow..."),
            _turn(
                2,
                utterance="How do we log a non-conformance report?",
                response="To log an NCR...",
            ),
            _turn(
                3,
                utterance="Cual es el procedimiento oficial para solicitar vacaciones?",
                response="Para solicitar vacaciones...",
            ),
        ]
        priors = prior_interactions(turns, "ix-1", limit=6)
        history = history_for_ai(priors)
        joined = " ".join(h["content"] for h in history)
        assert "hey" in joined
        assert "non-conformance" not in joined
        assert "vacaciones" not in joined
        assert "scarecrow" not in joined  # target turn itself excluded


class TestHistoryFormatters:
    def test_prior_responses_full_text(self):
        long = "x" * 500
        priors = [_turn(0, response=long)]
        assert prior_responses_for_heuristics(priors) == [long]

    def test_ai_history_truncates(self):
        long = "y" * 500
        priors = [_turn(0, utterance="q", response=long)]
        history = history_for_ai(priors, max_chars=AI_HISTORY_MAX_CHARS)
        assistant = [h for h in history if h["role"] == "assistant"][0]
        assert len(assistant["content"]) == AI_HISTORY_MAX_CHARS + 3  # + "..."
        assert assistant["content"].endswith("...")
        assert long[:AI_HISTORY_MAX_CHARS] in assistant["content"]
