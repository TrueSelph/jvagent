"""Tests for Ollama cumulative-resoning delta extraction."""

from jvagent.action.model.language.ollama.ollama import (
    _increment_ollama_thinking_stream,
)


def test_cumulative_thinking_emits_only_suffix():
    snap, d1 = _increment_ollama_thinking_stream("", "Hello")
    assert d1 == "Hello"
    snap, d2 = _increment_ollama_thinking_stream(snap, "Hello world")
    assert d2 == " world"
    snap, d3 = _increment_ollama_thinking_stream(snap, "Hello world!")
    assert d3 == "!"
    assert snap == "Hello world!"


def test_additive_fragments_when_not_prefix():
    snap, d1 = _increment_ollama_thinking_stream("", "Hello")
    snap, d2 = _increment_ollama_thinking_stream(snap, " world")
    assert d1 == "Hello"
    assert d2 == " world"
    assert snap == "Hello world"


def test_duplicate_emission_skipped():
    snap, d1 = _increment_ollama_thinking_stream("", "same")
    snap, d2 = _increment_ollama_thinking_stream(snap, "same")
    assert d1 == "same"
    assert d2 == ""
