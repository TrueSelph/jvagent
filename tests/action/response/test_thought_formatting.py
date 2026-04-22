"""Tests for thought flush-time text normalization."""

from jvagent.action.response.thought_formatting import (
    normalize_thought_text_for_publish,
)


def test_normalize_collapses_excessive_blank_lines():
    raw = "a\n\n\n\nb"
    assert normalize_thought_text_for_publish(raw) == "a\n\nb"


def test_normalize_strips_trailing_line_whitespace():
    raw = "line one   \nline two\t"
    assert normalize_thought_text_for_publish(raw) == "line one\nline two"


def test_normalize_normalizes_crlf():
    raw = "x\r\ny\rz"
    assert normalize_thought_text_for_publish(raw) == "x\ny\nz"
