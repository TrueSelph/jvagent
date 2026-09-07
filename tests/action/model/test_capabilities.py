"""Capability registry and metadata-sourced pricing (ADR-0045, Phase 2)."""

from __future__ import annotations

import pytest

from jvagent.action.model.capabilities import (
    _normalise_model_id,
    bundled_capabilities,
    clear_capability_cache,
    litellm_capabilities,
    override_capabilities,
    resolve_capabilities,
)
from jvagent.action.model.contract import ModelCapabilities
from jvagent.action.model.cost_estimator import estimate_cost, pricing_for

litellm = pytest.importorskip("litellm")


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_capability_cache()
    yield
    clear_capability_cache()


def test_model_id_normalisation_drops_provider_prefix_and_tag():
    assert _normalise_model_id("ollama/llama3.1:8b") == "llama3.1"
    assert _normalise_model_id("anthropic/claude-sonnet-4-5") == "claude-sonnet-4-5"
    assert _normalise_model_id("GPT-4o-mini") == "gpt-4o-mini"
    assert _normalise_model_id("") == ""


def test_bundled_table_knows_mainstream_families_and_nothing_else():
    gpt = bundled_capabilities("gpt-4o-mini")
    assert gpt and gpt.supports_tools is True and gpt.context_window == 128_000
    claude = bundled_capabilities("claude-sonnet-4-5")
    assert (
        claude
        and claude.supports_thinking is True
        and claude.supports_json_mode is False
    )
    gemma = bundled_capabilities("ollama/gemma2:9b")
    assert gemma and gemma.supports_tools is False
    llama = bundled_capabilities("llama3.1")
    assert llama and llama.supports_tools is True and llama.context_window is None
    assert bundled_capabilities("totally-unknown-model") is None


def test_litellm_layer_reads_upstream_metadata_and_tolerates_unknowns():
    caps = litellm_capabilities("gpt-4o-mini", "openai")
    assert caps and caps.source == "litellm"
    assert caps.supports_tools is True and caps.max_output_tokens == 16_384
    assert litellm_capabilities("not-a-real-model-xyz", "openai") is None
    # cached: a second lookup returns the same object without re-querying
    assert litellm_capabilities("not-a-real-model-xyz", "openai") is None


def test_resolution_merges_field_wise_with_override_winning():
    caps = resolve_capabilities("gpt-4o-mini", "openai")
    assert caps.supports_tools is True and caps.context_window == 128_000
    assert "litellm" in caps.source and "bundled" in caps.source

    forced = resolve_capabilities(
        "gpt-4o-mini",
        "openai",
        overrides={"supports_tools": False, "context_window": "4096"},
    )
    assert forced.supports_tools is False and forced.context_window == 4096
    assert forced.supports_vision is True  # untouched fields keep the resolved value
    assert forced.source.startswith("override")

    unknown = resolve_capabilities("mystery-model", "acme")
    assert unknown == ModelCapabilities(source="unknown")


def test_override_layer_ignores_junk_and_unknown_keys():
    assert override_capabilities({}) is None
    assert override_capabilities("nope") is None
    layer = override_capabilities(
        {"supports_tools": 1, "context_window": "abc", "bogus": True}
    )
    assert layer and layer.supports_tools is True and layer.context_window is None


# --- pricing -------------------------------------------------------------------


def test_pricing_prefers_litellm_metadata_then_bundled_then_none():
    upstream = pricing_for("openai", "gpt-4o-mini")
    assert upstream and upstream.source == "litellm"
    assert upstream.input_per_million == pytest.approx(0.15)
    assert upstream.cached_read_multiplier == pytest.approx(0.5)
    bundled = pricing_for("anthropic", "claude-3-5-sonnet")
    assert bundled and bundled.source == "bundled" and bundled.input_per_million == 3.0
    assert pricing_for("acme", "mystery") is None
    assert pricing_for("litellm", "anthropic/claude-sonnet-4-5").source == "litellm"


def test_estimate_cost_uses_metadata_pricing_with_cache_discount():
    million = {"prompt_tokens": 1_000_000, "completion_tokens": 0}
    assert estimate_cost("gpt-4o-mini", "openai", million) == pytest.approx(0.15)
    cached = {
        "prompt_tokens": 1_000_000,
        "completion_tokens": 0,
        "prompt_tokens_details": {"cached_tokens": 1_000_000},
    }
    assert estimate_cost("gpt-4o-mini", "openai", cached) == pytest.approx(0.075)
    # A LiteLLM-form id on the universal adapter is priced too.
    assert estimate_cost("openai/gpt-4o-mini", "litellm", million) == pytest.approx(
        0.15
    )
    # Ollama is free in upstream metadata.
    assert estimate_cost("llama3.1", "ollama", million) == 0.0


# --- issue #203: an inferred False is unknown ------------------------------


def _fake_litellm(monkeypatch, info, table):
    monkeypatch.setattr(litellm, "get_model_info", lambda *a, **k: dict(info))
    monkeypatch.setattr(litellm, "model_cost", dict(table))


def test_inferred_supports_tools_false_is_dropped_to_unknown(monkeypatch):
    """LiteLLM's Ollama provider answers ``supports_function_calling`` by
    searching the model's /api/show template for the word "tools"; Ollama
    Cloud models expose none. A False that is not in LiteLLM's table is a guess
    and must not choose the protocol."""
    _fake_litellm(
        monkeypatch,
        {"supports_function_calling": False, "max_input_tokens": 1_048_576},
        table={},
    )
    caps = litellm_capabilities("ollama/glm-5.3:cloud", "litellm")
    assert caps is not None
    assert caps.supports_tools is None
    assert caps.context_window == 1_048_576
    assert caps.source == "litellm(-tools)"
    resolved = resolve_capabilities("ollama/glm-5.3:cloud")
    assert resolved.supports_tools is None


def test_an_explicit_false_in_the_table_is_believed(monkeypatch):
    _fake_litellm(
        monkeypatch,
        {"supports_function_calling": False, "max_input_tokens": 8192},
        table={"ollama/some-embedding-ish-model": {}},
    )
    caps = litellm_capabilities("ollama/some-embedding-ish-model", "litellm")
    assert caps and caps.supports_tools is False and caps.source == "litellm"


def test_bare_id_in_the_table_also_counts_as_explicit(monkeypatch):
    _fake_litellm(
        monkeypatch,
        {"supports_function_calling": False},
        table={"text-embedding-3-small": {}},
    )
    caps = litellm_capabilities("openai/text-embedding-3-small", "litellm")
    assert caps and caps.supports_tools is False


def test_inferred_true_is_still_true(monkeypatch):
    _fake_litellm(monkeypatch, {"supports_function_calling": True}, table={})
    caps = litellm_capabilities("ollama/llama3.1:70b", "litellm")
    assert caps and caps.supports_tools is True and caps.source == "litellm"
