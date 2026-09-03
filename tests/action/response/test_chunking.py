"""Regression tests for simulated streaming chunk integrity."""

from jvagent.action.response.chunking import chunk_text_by_lm_tokens


def test_chunk_text_by_lm_tokens_preserves_cjk_and_emoji():
    samples = ["Bonjour 世界", "नमस्ते", "hi 🎉", "plain ascii"]
    for text in samples:
        out = "".join(chunk_text_by_lm_tokens(text))
        assert out == text, f"corrupted {text!r} -> {out!r}"
