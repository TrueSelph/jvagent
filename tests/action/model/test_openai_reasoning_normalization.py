"""Tests for OpenAI reasoning content shaping."""

from jvagent.action.model.language.openai.openai import OpenAILanguageModelAction


def test_normalize_reasoning_list_joins_with_newlines():
    raw = [
        {"text": "first"},
        {"content": "second"},
        "third",
    ]
    out = OpenAILanguageModelAction._normalize_reasoning_content(raw)
    assert out == "first\nsecond\nthird"


def test_normalize_reasoning_empty_list():
    assert OpenAILanguageModelAction._normalize_reasoning_content([]) == ""
