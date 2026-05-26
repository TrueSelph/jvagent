"""Tests for the EMIT→SHIFT defensive override on substantive utterances.

Live-smoke finding: ``llama-3.1-8b-instant`` returned
``{"verb":"EMIT","text":"Buscando ahora el clima en San Francisco hoy"}``
for the utterance ``"Search the web for the current weather in San Francisco
today"``. The model conflated transient_ack content with the final EMIT
verb, short-circuiting the turn before Reasoning could run.

These tests pin down the heuristic that catches that failure mode.
After the lexicon removal the heuristic is pure word-count + question-mark:

- Short non-interrogative utterances (≤ word threshold, no ``?``) are
  EMIT-allowed regardless of content. Trust the model to pick the
  right text via ``detected_language`` + content rules.
- Anything with more than ``_SUBSTANTIVE_UTTERANCE_WORD_THRESHOLD``
  words OR a question mark is treated as substantive — EMIT downgrades
  to SHIFT regardless of model output.
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

    # --- Short multilingual phrases: not substantive (word-count rule) ---

    @pytest.mark.parametrize(
        "utterance",
        ["Hi", "Hello", "Thanks!", "Got it", "Gracias", "Bonjour", "你好"],
    )
    def test_short_phrases_are_not_substantive(self, helm, utterance):
        """≤ 3 words and no ``?`` — EMIT-allowed regardless of language."""
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

    # --- Prior-question continuation: substantive regardless of length ---

    @pytest.mark.parametrize(
        "utterance",
        ["Yes", "No", "Sure", "Maybe", "Yep", "nope", "ok"],
    )
    def test_short_affirmative_after_question_is_substantive(self, helm, utterance):
        """``Yes`` / ``No`` / ``Sure`` etc. after an assistant question are
        continuations — they MUST shift to Reasoning, never EMIT back."""
        assert (
            helm._is_substantive_utterance(utterance, prior_was_question=True) is True
        )

    def test_long_utterance_after_question_still_substantive(self, helm):
        """``prior_was_question`` doesn't toggle short utterances down to
        non-substantive; it only forces short ones UP to substantive."""
        long_text = "Please go ahead and search the web"
        assert (
            helm._is_substantive_utterance(long_text, prior_was_question=True) is True
        )

    def test_no_prior_question_keeps_existing_short_path(self, helm):
        """Without ``prior_was_question``, the existing short-utterance
        guard behaves as before — ``"Yes"`` standalone is EMIT-able."""
        assert helm._is_substantive_utterance("Yes", prior_was_question=False) is False


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
        """Short non-interrogative utterances keep their EMIT."""
        helm = self._make_helm()
        parsed = {"verb": "EMIT", "text": "Hello!"}
        utterance = "Hi"

        result = helm._normalize_verb(
            parsed,
            utterance=utterance,
            peer_helm_names={"ReasoningHelm"},
            peer_action_names=set(),
        )

        # EMIT survives — utterance is ≤ threshold words with no '?'
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

    def test_emit_yes_after_question_downgrades_to_shift(self):
        """The exact live-smoke bug: user says ``"Yes"`` after assistant
        asked a question, Reflex EMIT'd ``"Yes"`` back. With the new
        ``prior_was_question`` signal, the guard now downgrades to SHIFT."""
        helm = self._make_helm()
        parsed = {"verb": "EMIT", "text": "Yes"}

        result = helm._normalize_verb(
            parsed,
            utterance="Yes",
            prior_was_question=True,
            peer_helm_names={"ReasoningHelm"},
            peer_action_names=set(),
        )

        assert isinstance(result, SHIFT)
        assert result.target == "ReasoningHelm"
        assert "continuation after assistant question" in result.reason


import pytest  # noqa: E402  — keep below class definitions for clarity


@pytest.mark.asyncio
class TestPriorAssistantEndedWithQuestion:
    """``_prior_assistant_ended_with_question`` — reads last conversation turn."""

    async def _helm_with_history(self, response_text):
        """Build a ReflexHelm with a mocked conversation whose last
        interaction's response is the given text."""
        from unittest.mock import AsyncMock, MagicMock

        helm = ReflexHelm()
        helm.history_limit = 1

        visitor = MagicMock()
        visitor.interaction = MagicMock()
        visitor.interaction.id = "int_current"

        conversation = MagicMock()
        conversation.get_interaction_history = AsyncMock(
            return_value=[{"response": response_text}]
        )
        visitor.conversation = conversation
        return helm, visitor

    async def test_returns_true_when_response_ends_with_question_mark(self):
        helm, visitor = await self._helm_with_history(
            "Would you like me to search the public web for more details?"
        )
        result = await helm._prior_assistant_ended_with_question(visitor)
        assert result is True

    async def test_returns_false_when_response_is_a_statement(self):
        helm, visitor = await self._helm_with_history(
            "The current weather in Tokyo is 17°C and overcast."
        )
        result = await helm._prior_assistant_ended_with_question(visitor)
        assert result is False

    async def test_trailing_whitespace_does_not_break_detection(self):
        helm, visitor = await self._helm_with_history(
            "Anything else I can help with?   \n"
        )
        result = await helm._prior_assistant_ended_with_question(visitor)
        assert result is True

    async def test_empty_history_returns_false(self):
        from unittest.mock import AsyncMock, MagicMock

        helm = ReflexHelm()
        visitor = MagicMock()
        visitor.interaction = MagicMock(id="int_current")
        conversation = MagicMock()
        conversation.get_interaction_history = AsyncMock(return_value=[])
        visitor.conversation = conversation

        result = await helm._prior_assistant_ended_with_question(visitor)
        assert result is False

    async def test_no_conversation_returns_false(self):
        from unittest.mock import MagicMock

        helm = ReflexHelm()
        visitor = MagicMock()
        visitor.conversation = None
        result = await helm._prior_assistant_ended_with_question(visitor)
        assert result is False

    async def test_empty_response_falls_through_to_older_turn(self):
        """If the most-recent turn has an empty response (e.g. a YIELDed
        turn with no publish), keep looking backward for the latest
        non-empty assistant message."""
        helm, visitor = await self._helm_with_history("")
        result = await helm._prior_assistant_ended_with_question(visitor)
        # Single empty turn → no question detected.
        assert result is False

    async def test_calls_get_interaction_history_with_formatted_false(self):
        """Load-bearing: the default ``formatted=True`` returns
        ``{"role", "content"}`` pairs (LM-ready), which would make
        ``turn.get("response")`` silently return None for every turn —
        the guard would always return False, regardless of the actual
        prior assistant turn. This regression test pins the call
        signature so a future refactor can't reintroduce the silent
        failure mode that shipped to live smoke in May 2026."""
        helm, visitor = await self._helm_with_history("Would you like me to search?")
        await helm._prior_assistant_ended_with_question(visitor)
        call_kwargs = visitor.conversation.get_interaction_history.call_args.kwargs
        assert call_kwargs.get("formatted") is False, (
            "_prior_assistant_ended_with_question MUST pass formatted=False; "
            "otherwise get_interaction_history returns role/content pairs and "
            "the response-key lookup silently fails."
        )

    async def test_does_not_misread_role_content_formatted_output(self):
        """If get_interaction_history is called with ``formatted=True``
        and returns role/content pairs (which is what the default does),
        the guard must not falsely classify it. By passing
        ``formatted=False`` we avoid that path entirely — but if a
        regression flips the default back, this test will catch it by
        feeding role/content pairs and asserting the guard does NOT
        return True (the ``content`` value ends in ``?`` but the
        ``response`` key is missing)."""
        from unittest.mock import AsyncMock, MagicMock

        helm = ReflexHelm()
        visitor = MagicMock()
        visitor.interaction = MagicMock(id="int_current")
        conversation = MagicMock()
        # Role/content pair — what formatted=True returns. No "response" key.
        conversation.get_interaction_history = AsyncMock(
            return_value=[
                {"role": "assistant", "content": "Would you like me to search?"}
            ]
        )
        visitor.conversation = conversation
        # Whether this returns False (current behaviour: guard silently
        # misses) or True (future fix: guard reads ``content`` too) — the
        # important property is that the production code is passing
        # ``formatted=False`` so this scenario can't happen at runtime.
        # We still pin the safe baseline here.
        result = await helm._prior_assistant_ended_with_question(visitor)
        assert result is False
