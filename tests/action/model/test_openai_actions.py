"""Tests for OpenAI language model action behavior."""

import pytest

from jvagent.action.model.language.openai.openai import OpenAILanguageModelAction


def test_estimate_cost_uses_effective_model_override():
    action = OpenAILanguageModelAction()
    action.total_cost = 0.0
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}

    action._estimate_cost(usage, model_name="gpt-4o-mini")
    mini_cost = action.total_cost

    action.total_cost = 0.0
    action._estimate_cost(usage, model_name="gpt-4o")
    gpt4o_cost = action.total_cost

    assert mini_cost > 0
    assert gpt4o_cost > mini_cost


# --- prompt-cache accounting ------------------------------------------------
#
# OpenAI caches long stable prefixes automatically and reports the hit in a
# nested prompt_tokens_details dict. The usage flattening dropped it, so an
# agentic caller had no way to see whether its prompt prefix was being reused
# -- and cost was overstated, since cached input bills at half rate.


def test_cached_prompt_tokens_read_from_details():
    extract = OpenAILanguageModelAction._cached_prompt_tokens
    assert extract({"prompt_tokens_details": {"cached_tokens": 1536}}) == 1536


def test_cached_prompt_tokens_absent_on_backends_without_caching():
    """Groq / OpenRouter / ollama speak the OpenAI wire format but report no
    details block; that must read as zero, not raise."""
    extract = OpenAILanguageModelAction._cached_prompt_tokens
    assert extract({}) == 0
    assert extract({"prompt_tokens_details": None}) == 0
    assert extract({"prompt_tokens_details": {}}) == 0
    assert extract({"prompt_tokens_details": {"cached_tokens": "junk"}}) == 0


def test_cached_tokens_lower_the_estimated_cost():
    action = OpenAILanguageModelAction()

    action.total_cost = 0.0
    action._estimate_cost(
        {"prompt_tokens": 1_000_000, "completion_tokens": 0}, model_name="gpt-4o"
    )
    uncached = action.total_cost

    action.total_cost = 0.0
    action._estimate_cost(
        {
            "prompt_tokens": 1_000_000,
            "completion_tokens": 0,
            "prompt_tokens_details": {"cached_tokens": 1_000_000},
        },
        model_name="gpt-4o",
    )
    fully_cached = action.total_cost

    assert fully_cached < uncached
    assert fully_cached == pytest.approx(uncached * 0.5, rel=1e-6)
