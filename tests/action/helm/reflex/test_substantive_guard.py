"""Tests for the EMIT→SHIFT defensive override on substantive utterances.

Live-smoke finding: ``llama-3.1-8b-instant`` returned
``{"verb":"EMIT","text":"Buscando ahora el clima en San Francisco hoy"}``
for the utterance ``"Search the web for the current weather in San Francisco
today"``. The model conflated transient_ack content with the final EMIT
verb, short-circuiting the turn before Reasoning could run.

These tests pin down the heuristic that catches that failure mode:

- Lexicon-matched short phrases (``Hi``, ``Thanks``, ``Gracias``...) are
  always EMIT-allowed regardless of word count.
- Anything else with more than ``_SUBSTANTIVE_UTTERANCE_WORD_THRESHOLD``
  words OR a question mark is treated as substantive — EMIT downgrades
  to SHIFT.
"""

from __future__ import annotations

import pytest

from jvagent.action.helm.contracts import EMIT, SHIFT
from jvagent.action.helm.reflex.reflex_helm import ReflexHelm


class TestIsSubstantiveUtterance:
    """``_is_substantive_utterance`` — the gate predicate."""

    @pytest.fixture
    def helm(self):
        return ReflexHelm()

    # --- Lexicon-matched: not substantive ---

    @pytest.mark.parametrize(
        "utterance",
        ["Hi", "Hello", "Thanks!", "Got it", "Gracias", "Bonjour", "你好"],
    )
    def test_lexicon_phrases_are_not_substantive(self, helm, utterance):
        assert helm._is_substantive_utterance(utterance) is False

    # --- Short and non-question: not substantive ---

    @pytest.mark.parametrize(
        "utterance",
        ["yes please", "ok cool", "got that"],
    )
    def test_short_non_question_not_substantive(self, helm, utterance):
        # ≤3 words, no question mark — model can EMIT
        assert helm._is_substantive_utterance(utterance) is False

    # --- Long: substantive ---

    @pytest.mark.parametrize(
        "utterance",
        [
            "Search the web for the current weather in San Francisco today",
            "Tell me about quantum mechanics",
            "Make a plan to migrate the database",
        ],
    )
    def test_long_utterances_are_substantive(self, helm, utterance):
        assert helm._is_substantive_utterance(utterance) is True

    # --- Question mark: substantive regardless of length ---

    @pytest.mark.parametrize(
        "utterance",
        ["What is 2+2?", "Why?", "Where am I?", "How does this work?"],
    )
    def test_questions_are_substantive(self, helm, utterance):
        # Any "?" → substantive, even ≤3 words
        assert helm._is_substantive_utterance(utterance) is True

    # --- Edge cases ---

    def test_empty_is_not_substantive(self, helm):
        assert helm._is_substantive_utterance("") is False

    def test_whitespace_only_is_not_substantive(self, helm):
        assert helm._is_substantive_utterance("   \n\t  ") is False

    def test_three_word_non_question_is_not_substantive(self, helm):
        # Right at the threshold — model can still EMIT
        assert helm._is_substantive_utterance("yes I will") is False


class TestEmitDowngradesOnSubstantive:
    """``_normalize_verb`` downgrades EMIT→SHIFT for substantive utterances."""

    def _make_helm(self):
        helm = ReflexHelm()
        # Test config: default_shift_target must resolve in peer_helm_names
        helm.default_shift_target = "ReasoningHelm"
        return helm

    def test_substantive_emit_downgrades_to_shift(self):
        """The exact failure mode observed in live smoke."""
        helm = self._make_helm()
        parsed = {
            "verb": "EMIT",
            "text": "Buscando ahora el clima en San Francisco hoy",
        }
        utterance = "Search the web for the current weather in San Francisco today"

        result = helm._normalize_verb(
            parsed,
            utterance=utterance,
            peer_helm_names={"ReasoningHelm"},
            peer_action_names=set(),
        )

        # Defensive override engaged — SHIFT, not EMIT
        assert isinstance(result, SHIFT)
        assert result.target == "ReasoningHelm"
        # Reason captures why the override fired (for log triage)
        assert "EMIT on substantive utterance" in result.reason

    def test_trivial_emit_still_passes_through(self):
        """Lexicon-matched short phrases keep their EMIT."""
        helm = self._make_helm()
        parsed = {"verb": "EMIT", "text": "Hello!"}
        utterance = "Hi"

        result = helm._normalize_verb(
            parsed,
            utterance=utterance,
            peer_helm_names={"ReasoningHelm"},
            peer_action_names=set(),
        )

        # EMIT survives — utterance is in lexicon
        assert isinstance(result, EMIT)
        assert result.text == "Hello!"
        assert result.finalize is True

    def test_question_emit_downgrades_to_shift(self):
        """Short questions like 'What is 2+2?' still go to Reasoning."""
        helm = self._make_helm()
        parsed = {"verb": "EMIT", "text": "4"}
        utterance = "What is 2+2?"

        result = helm._normalize_verb(
            parsed,
            utterance=utterance,
            peer_helm_names={"ReasoningHelm"},
            peer_action_names=set(),
        )

        assert isinstance(result, SHIFT)
        assert result.target == "ReasoningHelm"

    def test_shift_unaffected_by_guard(self):
        """SHIFT verbs pass through regardless of utterance length."""
        helm = self._make_helm()
        parsed = {
            "verb": "SHIFT",
            "target": "ReasoningHelm",
            "reason": "needs reasoning",
            "transient_ack": "Looking that up…",
        }
        utterance = "Search the web for the current weather in San Francisco today"

        result = helm._normalize_verb(
            parsed,
            utterance=utterance,
            peer_helm_names={"ReasoningHelm"},
            peer_action_names=set(),
        )

        assert isinstance(result, SHIFT)
        assert result.transient_ack == "Looking that up…"
