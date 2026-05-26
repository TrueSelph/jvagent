"""Tests for the Reflex short-utterance language detector.

The detector is deliberately bounded: it only resolves SHORT phrases
that match a known lexicon entry. Long utterances return ``None`` so the
model handles language inference on its own.
"""

from __future__ import annotations

import pytest

from jvagent.action.helm.reflex.language import (
    detect_short_utterance_language,
    language_hint_line,
)


class TestDetectShortUtteranceLanguage:
    """Cover the bounded short-greeting / thanks / confirmation cases."""

    @pytest.mark.parametrize(
        ("utterance", "expected"),
        [
            # English — these are the cases that triggered the bug
            ("Hi", "English"),
            ("Hi!", "English"),
            ("hello", "English"),
            ("Hey", "English"),
            ("Thanks", "English"),
            ("Thanks!", "English"),
            ("thank you", "English"),
            ("ok", "English"),
            ("Got it", "English"),
            ("yes", "English"),
            ("nope", "English"),
            # Spanish
            ("Hola", "Spanish"),
            ("¡Hola!", "Spanish"),
            ("gracias", "Spanish"),
            ("buenas tardes", "Spanish"),
            ("sí", "Spanish"),
            # French
            ("Bonjour", "French"),
            ("Merci", "French"),
            ("merci beaucoup", "French"),
            # German
            ("Hallo", "German"),
            ("Danke", "German"),
            ("Tschüss", "German"),
            # Italian
            ("Ciao", "Italian"),
            ("Grazie", "Italian"),
            # Portuguese
            ("Olá", "Portuguese"),
            ("Obrigado", "Portuguese"),
            # Japanese
            ("こんにちは", "Japanese"),
            ("ありがとう", "Japanese"),
            # Chinese
            ("你好", "Chinese"),
            ("谢谢", "Chinese"),
        ],
    )
    def test_known_phrases_resolve_to_language(self, utterance, expected):
        assert detect_short_utterance_language(utterance) == expected

    @pytest.mark.parametrize(
        "utterance",
        [
            "",
            "   ",
            "!!!",
            "?",
        ],
    )
    def test_empty_or_punctuation_only_returns_none(self, utterance):
        assert detect_short_utterance_language(utterance) is None

    def test_long_utterance_returns_none(self):
        """Beyond the lexicon's bounded scope — model handles it."""
        long_text = "Search the web for the current weather in San Francisco today"
        assert detect_short_utterance_language(long_text) is None

    def test_unknown_short_phrase_returns_none(self):
        """A real word that's not in the lexicon falls through."""
        assert detect_short_utterance_language("supercalifragilistic") is None

    def test_case_insensitive(self):
        """Match is case-folded so 'HELLO' and 'hello' resolve the same."""
        assert detect_short_utterance_language("HELLO") == "English"
        assert detect_short_utterance_language("Hola") == "Spanish"

    def test_punctuation_stripping(self):
        """Leading + trailing punctuation doesn't break matching."""
        assert detect_short_utterance_language("...hi") == "English"
        assert detect_short_utterance_language("hi...") == "English"
        assert detect_short_utterance_language("¡Hola!") == "Spanish"


class TestLanguageHintLine:
    """The format string spliced into Reflex's user prompt."""

    def test_none_returns_empty_string(self):
        """No detection → empty string; the prompt slot stays clean."""
        assert language_hint_line(None) == ""

    def test_empty_string_returns_empty_string(self):
        assert language_hint_line("") == ""

    def test_detected_language_renders_directive(self):
        line = language_hint_line("English")
        assert "English" in line
        assert "Reply in English" in line
        assert line.endswith("\n")

    def test_directive_for_spanish(self):
        line = language_hint_line("Spanish")
        assert "Spanish" in line
        assert "Reply in Spanish" in line
