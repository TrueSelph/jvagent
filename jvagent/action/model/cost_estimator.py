"""Shared cost estimation utility for model calls.

Estimates USD cost from (model, provider, usage) using known pricing tables.
Used by Interaction.compute_usage() and other consumers that need
per-call cost estimation from observability event data.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from jvagent.action.model.contract import Pricing

# Pricing per 1M tokens (USD). Keys: model identifier. Values: {"input": float, "output": float}
# For embeddings, "output" is typically 0 or same as input (single rate).
_LLM_PRICING: Dict[str, Dict[str, float]] = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.150, "output": 0.600},
    "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
}

_LLM_PRICING_BY_PROVIDER: Dict[str, Dict[str, Dict[str, float]]] = {
    "openai": _LLM_PRICING,
    "openrouter": {
        "gpt-4o": {"input": 2.50, "output": 10.00},
        "gpt-4o-mini": {"input": 0.150, "output": 0.600},
        "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
        "claude-3-7-sonnet": {"input": 3.00, "output": 15.00},
    },
    "anthropic": {
        "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
        "claude-3-7-sonnet": {"input": 3.00, "output": 15.00},
    },
    "ollama": {},
}

# Embedding models: single rate per 1M tokens
_EMBEDDING_PRICING: Dict[str, float] = {
    "text-embedding-3-small": 0.02,
    "text-embedding-3-large": 0.13,
    "text-embedding-ada-002": 0.10,
}

# Generic fallback when model not in tables (per 1M tokens)
_DEFAULT_INPUT_RATE = 1.0
_DEFAULT_OUTPUT_RATE = 2.0
_DEFAULT_EMBEDDING_RATE = 0.10
_UNKNOWN_PROVIDER_WARNED: set[str] = set()

# Prompt-cache token rates, as multipliers on a model's normal input rate.
# Cached input is not free and not full price, and an agentic loop resends a
# large stable prefix on every tick — so charging every input token at the full
# rate overstates the cost of exactly the calls a well-ordered prompt is
# designed to make cheap.
#
# ``read`` is a cache hit; ``write`` is the one-time cost of establishing the
# entry (a premium on Anthropic, free on OpenAI's automatic caching). Unknown
# providers default to no discount — better to overstate cost than understate it.
_CACHE_RATES_BY_PROVIDER: Dict[str, Dict[str, float]] = {
    "openai": {"read": 0.5, "write": 1.0},
    "openrouter": {"read": 0.5, "write": 1.0},
    "anthropic": {"read": 0.1, "write": 1.25},
}
_DEFAULT_CACHE_RATES: Dict[str, float] = {"read": 1.0, "write": 1.0}


def cache_rates_for_provider(provider: str) -> Dict[str, float]:
    """Cache read/write multipliers on the input rate for *provider*."""
    return _CACHE_RATES_BY_PROVIDER.get(
        (provider or "").strip().lower(), _DEFAULT_CACHE_RATES
    )


def split_cached_prompt_tokens(usage: Dict[str, Any]) -> tuple:
    """Split ``prompt_tokens`` into ``(uncached, cache_read, cache_write)``.

    Both providers report cached counts as a *subset* of the prompt tokens
    (jvagent's Anthropic action folds its separately-reported cache counters in),
    so these are carved out of the total rather than added to it. Reads are
    ``cached_tokens`` (OpenAI, flattened) or ``prompt_tokens_details.cached_tokens``
    (OpenAI, raw) or ``cache_read_input_tokens`` (Anthropic).
    """

    def _int(key: str) -> int:
        try:
            return max(0, int(usage.get(key, 0) or 0))
        except (TypeError, ValueError):
            return 0

    prompt_tokens = _int("prompt_tokens")
    nested = 0
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        try:
            nested = max(0, int(details.get("cached_tokens", 0) or 0))
        except (TypeError, ValueError):
            nested = 0
    read = min(
        _int("cached_tokens") or _int("cache_read_input_tokens") or nested,
        prompt_tokens,
    )
    write = min(_int("cache_creation_input_tokens"), prompt_tokens - read)
    return prompt_tokens - read - write, read, write


_litellm_pricing_cache: Dict[Tuple[str, str], Optional["Pricing"]] = {}


def _litellm_pricing(provider: str, model: str) -> Optional["Pricing"]:
    """Per-million pricing from ``litellm.get_model_info`` when LiteLLM is
    installed and knows the model; ``None`` otherwise. Cached per process."""
    from jvagent.action.model.contract import Pricing

    key = (str(provider or ""), str(model or ""))
    if key in _litellm_pricing_cache:
        return _litellm_pricing_cache[key]
    result: Optional[Pricing] = None
    try:
        import litellm  # optional extra

        custom = (provider or "").strip().lower()
        if custom in ("", "litellm") or "/" in str(model):
            info = litellm.get_model_info(model)
        else:
            info = litellm.get_model_info(model, custom_llm_provider=custom)
        inp = info.get("input_cost_per_token") if isinstance(info, dict) else None
        out = info.get("output_cost_per_token") if isinstance(info, dict) else None
        if inp is not None and out is not None:
            inp_f, out_f = float(inp), float(out)
            read = info.get("cache_read_input_token_cost")
            write = info.get("cache_creation_input_token_cost")
            result = Pricing(
                input_per_million=inp_f * 1_000_000,
                output_per_million=out_f * 1_000_000,
                cached_read_multiplier=(
                    (float(read) / inp_f) if (read is not None and inp_f) else 1.0
                ),
                cached_write_multiplier=(
                    (float(write) / inp_f) if (write is not None and inp_f) else 1.0
                ),
                source="litellm",
            )
    except ImportError:
        result = None
    except Exception as exc:  # unmapped model — fall back to the bundled table
        logger.debug(
            "pricing: litellm has no entry for %r/%r: %s", provider, model, exc
        )
        result = None
    _litellm_pricing_cache[key] = result
    return result


def _bundled_pricing(provider: str, model: str) -> Optional["Pricing"]:
    from jvagent.action.model.contract import Pricing

    provider_key = (provider or "").strip().lower()
    lookup_model = model.split("/")[-1] if "/" in model else model
    table = _LLM_PRICING_BY_PROVIDER.get(provider_key) or (
        _LLM_PRICING if provider_key in ("", "litellm") else {}
    )
    entry = table.get(lookup_model) or table.get(model)
    if not entry:
        for key, value in table.items():
            if key and lookup_model.startswith(key):
                entry = value
                break
    if not entry:
        return None
    rates = cache_rates_for_provider(provider)
    return Pricing(
        input_per_million=float(entry.get("input", 0.0)),
        output_per_million=float(entry.get("output", 0.0)),
        cached_read_multiplier=float(rates.get("read", 1.0)),
        cached_write_multiplier=float(rates.get("write", 1.0)),
        source="bundled",
    )


def pricing_for(provider: str, model: str) -> Optional["Pricing"]:
    """Effective pricing for ``model`` on ``provider``: LiteLLM metadata when
    available (maintained upstream for hundreds of models), else the bundled
    table, else ``None`` (the caller decides on a conservative default)."""
    return _litellm_pricing(provider, model) or _bundled_pricing(provider, model)


def clear_pricing_cache() -> None:
    _litellm_pricing_cache.clear()


def estimate_cost(
    model: str,
    provider: str,
    usage: Dict[str, Any],
    event_type: str = "model_call",
) -> float:
    """Estimate cost in USD for a model call.

    Args:
        model: Model identifier (e.g., 'gpt-4o-mini', 'text-embedding-3-small')
        provider: Provider name (e.g., 'openai', 'openrouter')
        usage: Usage dict with prompt_tokens, completion_tokens, and/or total_tokens
        event_type: 'model_call' or 'embedding_call'

    Returns:
        Estimated cost in USD
    """
    if not usage:
        return 0.0

    # Normalize model for lookup (OpenRouter uses provider/model format)
    lookup_model = model.split("/")[-1] if "/" in model else model

    provider_key = (provider or "").strip().lower()

    if event_type == "embedding_call":
        rate = _EMBEDDING_PRICING.get(lookup_model, _DEFAULT_EMBEDDING_RATE)
        total_tokens = usage.get("total_tokens", 0) or 0
        return (total_tokens / 1_000_000) * rate

    # LLM: separate input/output. Metadata-sourced pricing first (LiteLLM's
    # table when installed, per-provider bundled table otherwise) — this is
    # what makes cost policy real for models the four-entry dict never knew.
    priced = pricing_for(provider, model)
    if priced is not None:
        prompt_tokens = usage.get("prompt_tokens", 0) or 0
        completion_tokens = usage.get("completion_tokens", 0) or 0
        if prompt_tokens == 0 and completion_tokens == 0:
            total = usage.get("total_tokens", 0) or 0
            return (total / 1_000_000) * priced.input_per_million
        uncached, cache_read, cache_write = split_cached_prompt_tokens(usage)
        prompt_cost = (
            (
                uncached
                + cache_read * priced.cached_read_multiplier
                + cache_write * priced.cached_write_multiplier
            )
            / 1_000_000
            * priced.input_per_million
        )
        return prompt_cost + (completion_tokens / 1_000_000) * priced.output_per_million

    if provider_key in {"openai", "openrouter", "anthropic", "ollama", "litellm"}:
        provider_pricing = _LLM_PRICING_BY_PROVIDER.get(provider_key, {})
        pricing = provider_pricing.get(lookup_model) or provider_pricing.get(model)
    else:
        pricing = _LLM_PRICING.get(lookup_model) or _LLM_PRICING.get(model)
        if provider_key and provider_key not in _UNKNOWN_PROVIDER_WARNED:
            logger.warning(
                "Unknown provider '%s' in estimate_cost(); returning 0 for model '%s'",
                provider,
                model,
            )
            _UNKNOWN_PROVIDER_WARNED.add(provider_key)
            return 0.0

    if not pricing:
        # Unknown model for known provider: conservative non-zero fallback.
        pricing = {"input": _DEFAULT_INPUT_RATE, "output": _DEFAULT_OUTPUT_RATE}

    prompt_tokens = usage.get("prompt_tokens", 0) or 0
    completion_tokens = usage.get("completion_tokens", 0) or 0
    # Fallback: use total_tokens if prompt/completion not split
    if prompt_tokens == 0 and completion_tokens == 0:
        total = usage.get("total_tokens", 0) or 0
        prompt_tokens = total  # Treat all as input for conservative estimate
        return (prompt_tokens / 1_000_000) * pricing["input"]

    # Cached prompt tokens bill at a fraction of the input rate (or, for an
    # Anthropic cache write, a premium). With no cache counters reported this
    # reduces exactly to the old flat calculation.
    uncached, cache_read, cache_write = split_cached_prompt_tokens(usage)
    rates = cache_rates_for_provider(provider)
    prompt_cost = (
        (uncached + cache_read * rates["read"] + cache_write * rates["write"])
        / 1_000_000
        * pricing["input"]
    )
    completion_cost = (completion_tokens / 1_000_000) * pricing["output"]
    return prompt_cost + completion_cost
