"""Shared cost estimation utility for model calls.

Estimates USD cost from (model, provider, usage) using known pricing tables.
Used by Interaction.compute_usage() and other consumers that need
per-call cost estimation from observability event data.
"""

import logging
import math
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
    # Ollama Cloud's metered token prices from https://ollama.com/pricing
    # (verified 2026-10-05). Local IDs without ``:cloud`` remain free here.
    "ollama": {
        "glm-5.3": {"input": 1.40, "output": 4.40, "cached_input": 0.26},
        "glm-5.3-flash": {"input": 0.15, "output": 0.50, "cached_input": 0.03},
        "glm-5.2": {"input": 1.40, "output": 4.40, "cached_input": 0.26},
    },
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


def is_valid_cost_usd(value: Any) -> bool:
    """Whether *value* is a finite, non-negative USD amount (including zero)."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) >= 0.0
    )


def reported_cost_record(metrics: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Build the provider-neutral record for an explicit cost receipt."""
    if "cost_usd" not in metrics or not is_valid_cost_usd(metrics.get("cost_usd")):
        return None
    source = str(metrics.get("cost_source") or "provider_reported")[:128]
    estimated = bool(metrics.get("cost_estimated")) or source == "jv_cost_estimator"
    return {
        "amount": float(metrics["cost_usd"]),
        "currency": "USD",
        "source": source,
        "estimated": estimated,
        "pricing_version": metrics.get("pricing_version"),
    }


def estimated_cost_record(
    model: str, provider: str, usage: Dict[str, Any], event_type: str
) -> Dict[str, Any]:
    """Estimate cost and retain the pricing provenance or unknown state."""
    estimate = estimate_cost(model, provider, usage, event_type)
    pricing = pricing_for(provider, model)
    if pricing is not None:
        source = "jv_cost_estimator"
        version = f"{pricing.source}-pricing-v1"
        amount: Optional[float] = estimate
    elif (provider or "").strip().lower() in {
        "openai",
        "openrouter",
        "anthropic",
        "ollama",
        "litellm",
        "",
    }:
        # estimate_cost deliberately applies its conservative fallback for
        # unknown model IDs on supported providers.
        source = "jv_cost_estimator"
        version = "conservative-fallback-v1"
        amount = estimate
    else:
        # An unknown provider's zero estimate is not evidence of free usage.
        source = "unavailable_pricing"
        version = None
        amount = None
    return {
        "amount": amount,
        "currency": "USD",
        "source": source,
        "estimated": True,
        "pricing_version": version,
    }


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
    if provider_key == "ollama" and lookup_model.endswith(":cloud"):
        lookup_model = lookup_model[: -len(":cloud")]
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
    cached_read_multiplier = float(rates.get("read", 1.0))
    if provider_key == "ollama" and model.endswith(":cloud"):
        cached_input = entry.get("cached_input")
        if cached_input is not None and entry.get("input"):
            cached_read_multiplier = float(cached_input) / float(entry["input"])
    return Pricing(
        input_per_million=float(entry.get("input", 0.0)),
        output_per_million=float(entry.get("output", 0.0)),
        cached_read_multiplier=cached_read_multiplier,
        cached_write_multiplier=float(rates.get("write", 1.0)),
        source="bundled",
    )


def pricing_for(provider: str, model: str) -> Optional["Pricing"]:
    """Effective pricing for ``model`` on ``provider``: LiteLLM metadata when
    available (maintained upstream for hundreds of models), else the bundled
    table, else ``None`` (the caller decides on a conservative default)."""
    if (provider or "").strip().lower() == "ollama" and model.endswith(":cloud"):
        return _bundled_pricing(provider, model)
    return _litellm_pricing(provider, model) or _bundled_pricing(provider, model)


def request_cost_reservation_usd(
    provider: str,
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    safety_multiplier: float = 1.25,
) -> Optional[float]:
    """Estimate a pessimistic request reservation from known model pricing.

    This is an application-side admission estimate, not a provider-enforced
    spend cap. Unknown prices return ``None`` so callers with a configured
    dollar ceiling can stop before sending a request rather than assume it is
    free. The input side uses the uncached rate or the larger cache-write rate;
    cache-read discounts are deliberately ignored.
    """
    if (
        isinstance(input_tokens, bool)
        or not isinstance(input_tokens, int)
        or input_tokens < 0
        or isinstance(output_tokens, bool)
        or not isinstance(output_tokens, int)
        or output_tokens < 0
        or isinstance(safety_multiplier, bool)
        or not isinstance(safety_multiplier, (int, float))
        or not math.isfinite(float(safety_multiplier))
        or safety_multiplier < 1.0
    ):
        raise ValueError(
            "request reservation limits must be finite non-negative values"
        )
    pricing = pricing_for(provider, model)
    if pricing is None:
        return None
    input_rate = float(pricing.input_per_million) * max(
        1.0, float(pricing.cached_write_multiplier)
    )
    output_rate = float(pricing.output_per_million)
    if (
        not math.isfinite(input_rate)
        or not math.isfinite(output_rate)
        or input_rate < 0
        or output_rate < 0
    ):
        return None
    estimated = (
        (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000
    ) * float(safety_multiplier)
    return estimated if math.isfinite(estimated) else None


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

    # A cloud-suffixed Ollama ID is a metered hosted model. LiteLLM may
    # identify Ollama IDs as local/free, so deliberately use Ollama's own
    # published cloud table before consulting its model metadata.
    if provider_key == "ollama" and model.endswith(":cloud"):
        priced = _bundled_pricing(provider_key, model)
        if priced is not None:
            prompt_tokens = usage.get("prompt_tokens", 0) or 0
            completion_tokens = usage.get("completion_tokens", 0) or 0
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
            if prompt_tokens == 0 and completion_tokens == 0:
                prompt_cost = (
                    usage.get("total_tokens", 0) / 1_000_000
                ) * priced.input_per_million
            return (
                prompt_cost
                + (completion_tokens / 1_000_000) * priced.output_per_million
            )

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
