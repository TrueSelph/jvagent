"""Shared cost estimation utility for model calls.

Estimates USD cost from (model, provider, usage) using known pricing tables.
Used by Interaction.compute_usage() and other consumers that need
per-call cost estimation from observability event data.
"""

from typing import Any, Dict

# Pricing per 1M tokens (USD). Keys: model identifier. Values: {"input": float, "output": float}
# For embeddings, "output" is typically 0 or same as input (single rate).
_LLM_PRICING: Dict[str, Dict[str, float]] = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.150, "output": 0.600},
    "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
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

    if event_type == "embedding_call":
        rate = _EMBEDDING_PRICING.get(lookup_model, _DEFAULT_EMBEDDING_RATE)
        total_tokens = usage.get("total_tokens", 0) or 0
        return (total_tokens / 1_000_000) * rate

    # LLM: separate input/output
    pricing = _LLM_PRICING.get(lookup_model) or _LLM_PRICING.get(model)
    if not pricing:
        pricing = {"input": _DEFAULT_INPUT_RATE, "output": _DEFAULT_OUTPUT_RATE}

    prompt_tokens = usage.get("prompt_tokens", 0) or 0
    completion_tokens = usage.get("completion_tokens", 0) or 0
    # Fallback: use total_tokens if prompt/completion not split
    if prompt_tokens == 0 and completion_tokens == 0:
        total = usage.get("total_tokens", 0) or 0
        prompt_tokens = total  # Treat all as input for conservative estimate

    prompt_cost = (prompt_tokens / 1_000_000) * pricing["input"]
    completion_cost = (completion_tokens / 1_000_000) * pricing["output"]
    return prompt_cost + completion_cost
