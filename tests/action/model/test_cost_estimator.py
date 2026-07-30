"""Tests for model cost estimation utility."""

import pytest

from jvagent.action.model.cost_estimator import (
    cache_rates_for_provider,
    estimate_cost,
    split_cached_prompt_tokens,
)


def test_estimate_cost_uses_provider_specific_prices():
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}
    openai_cost = estimate_cost("gpt-4o-mini", "openai", usage)
    anthropic_cost = estimate_cost("claude-3-5-sonnet", "anthropic", usage)

    assert openai_cost > 0
    assert anthropic_cost > openai_cost


def test_estimate_cost_unknown_provider_returns_zero():
    usage = {"prompt_tokens": 1000, "completion_tokens": 1000}
    assert estimate_cost("any-model", "unknown-provider", usage) == 0.0


# --- prompt-cache pricing --------------------------------------------------
#
# An agentic loop resends a large stable prefix on every tick, so cached input
# is the common case rather than the exception. Charging it at the full input
# rate overstates the cost of exactly the calls a well-ordered prompt is
# designed to make cheap.


def test_cached_input_costs_less_than_uncached():
    uncached = {"prompt_tokens": 1_000_000, "completion_tokens": 0}
    cached = {
        "prompt_tokens": 1_000_000,
        "completion_tokens": 0,
        "cached_tokens": 1_000_000,
    }
    assert estimate_cost("gpt-4o", "openai", cached) < estimate_cost(
        "gpt-4o", "openai", uncached
    )


def test_openai_cache_read_is_half_price():
    full = estimate_cost("gpt-4o", "openai", {"prompt_tokens": 1_000_000})
    half = estimate_cost(
        "gpt-4o",
        "openai",
        {
            "prompt_tokens": 1_000_000,
            "completion_tokens": 1,
            "cached_tokens": 1_000_000,
        },
    )
    # completion_tokens=1 is negligible against 1M input tokens.
    assert half == pytest.approx(full * 0.5, rel=1e-3)


def test_anthropic_cache_write_costs_a_premium():
    """Anthropic charges more to establish a cache entry than to send the
    tokens uncached -- so a write must never look cheaper than the baseline."""
    baseline = estimate_cost(
        "claude-3-5-sonnet",
        "anthropic",
        {"prompt_tokens": 1_000_000, "completion_tokens": 1},
    )
    write = estimate_cost(
        "claude-3-5-sonnet",
        "anthropic",
        {
            "prompt_tokens": 1_000_000,
            "completion_tokens": 1,
            "cache_creation_input_tokens": 1_000_000,
        },
    )
    read = estimate_cost(
        "claude-3-5-sonnet",
        "anthropic",
        {
            "prompt_tokens": 1_000_000,
            "completion_tokens": 1,
            "cache_read_input_tokens": 1_000_000,
        },
    )
    assert write > baseline > read


def test_unknown_provider_cache_rates_claim_no_discount():
    """Better to overstate cost than understate it for a provider whose cache
    economics we don't model."""
    assert cache_rates_for_provider("some-new-llm") == {"read": 1.0, "write": 1.0}


def test_usage_without_cache_counters_is_unchanged():
    """The flat calculation must be exactly preserved when nothing is cached."""
    usage = {"prompt_tokens": 500_000, "completion_tokens": 250_000}
    expected = (500_000 / 1e6) * 2.50 + (250_000 / 1e6) * 10.00
    assert estimate_cost("gpt-4o", "openai", usage) == pytest.approx(expected)


def test_cache_counters_are_clamped_to_prompt_tokens():
    """Cached counts are a subset of prompt_tokens; a bogus over-count must not
    produce a negative uncached remainder (and so a nonsense cost)."""
    uncached, read, write = split_cached_prompt_tokens(
        {"prompt_tokens": 100, "cached_tokens": 9999}
    )
    assert (uncached, read, write) == (0, 100, 0)
    assert (
        estimate_cost("gpt-4o", "openai", {"prompt_tokens": 100, "cached_tokens": 9999})
        > 0
    )


def test_split_tolerates_junk_values():
    assert split_cached_prompt_tokens({"prompt_tokens": "x"}) == (0, 0, 0)
    assert split_cached_prompt_tokens({"prompt_tokens": 10, "cached_tokens": None}) == (
        10,
        0,
        0,
    )
