"""Tests for OpenAI language model action behavior."""

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
