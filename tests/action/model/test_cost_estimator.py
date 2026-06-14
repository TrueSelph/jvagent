"""Tests for model cost estimation utility."""

from jvagent.action.model.cost_estimator import estimate_cost


def test_estimate_cost_uses_provider_specific_prices():
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}
    openai_cost = estimate_cost("gpt-4o-mini", "openai", usage)
    anthropic_cost = estimate_cost("claude-3-5-sonnet", "anthropic", usage)

    assert openai_cost > 0
    assert anthropic_cost > openai_cost


def test_estimate_cost_unknown_provider_returns_zero():
    usage = {"prompt_tokens": 1000, "completion_tokens": 1000}
    assert estimate_cost("any-model", "unknown-provider", usage) == 0.0
