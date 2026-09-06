"""Per-model capability registry (model remediation, Phase 2; ADR-0045).

Answers "what can this model do?" for the harness — whether it supports tool
calling, parallel tool calls, JSON mode / structured output, vision, extended
thinking, and how large its context window and output ceiling are — so the
Orchestrator can pick the decision protocol (``tool_protocol: auto``), clamp
``max_tokens`` and pre-flight the context budget instead of guessing.

Resolution order (field by field, first known value wins):

1. **Operator override** — ``model_capabilities`` on the language-model action
   (``agent.yaml``), for a model the sources below get wrong or do not know.
2. **LiteLLM metadata** — ``litellm.get_model_info`` when LiteLLM is
   installed (it is an optional extra; PageIndex already pulls it in). Maintained
   upstream for hundreds of models; cached per process.
3. **Bundled table** — conservative entries for the model families the
   first-party adapters ship with, so an install without LiteLLM still knows
   the mainstream models.
4. **Unknown** — ``None`` fields. Nothing is guessed; consumers treat unknown as
   "assume the mainstream default" (e.g. native tool calling) and never as a
   hard capability claim.
"""

from __future__ import annotations

import logging
import re
from dataclasses import fields
from typing import Any, Dict, Optional, Tuple

from jvagent.action.model.contract import ModelCapabilities

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Bundled table — (model-id regex, capabilities). Ordered most-specific first.
# Only values that are stable across a family are set; the rest stay unknown.
# --------------------------------------------------------------------------- #

_C = ModelCapabilities

_BUNDLED: Tuple[Tuple[str, ModelCapabilities], ...] = (
    # OpenAI
    (
        r"^gpt-5",
        _C(True, True, True, True, True, True, 400_000, 128_000, "bundled"),
    ),
    (
        r"^gpt-4\.1",
        _C(True, True, True, True, True, False, 1_047_576, 32_768, "bundled"),
    ),
    (
        r"^gpt-4o",
        _C(True, True, True, True, True, False, 128_000, 16_384, "bundled"),
    ),
    (
        r"^o[134](-mini|-pro|-preview)?(-|$)",
        _C(True, None, True, True, True, True, 200_000, 100_000, "bundled"),
    ),
    (
        r"^gpt-4-turbo",
        _C(True, True, True, False, True, False, 128_000, 4_096, "bundled"),
    ),
    (
        r"^gpt-3\.5-turbo",
        _C(True, True, True, False, False, False, 16_385, 4_096, "bundled"),
    ),
    # Anthropic (JSON mode/structured output are tool-based, not a response_format)
    (
        r"^claude-(sonnet|opus|haiku)-4",
        _C(True, True, False, False, True, True, 200_000, 64_000, "bundled"),
    ),
    (
        r"^claude-(4|3-7)",
        _C(True, True, False, False, True, True, 200_000, 64_000, "bundled"),
    ),
    (
        r"^claude-3-5",
        _C(True, True, False, False, True, False, 200_000, 8_192, "bundled"),
    ),
    (
        r"^claude-3-(haiku|opus|sonnet)",
        _C(True, True, False, False, True, False, 200_000, 4_096, "bundled"),
    ),
    # Open models commonly served by Ollama / Groq / OpenRouter. Context and
    # output ceilings vary per quantisation/serving config — left unknown.
    (
        r"^(llama-?3(\.[123])?|qwen(2\.5|3)|mistral|mixtral|command-r|hermes|"
        r"deepseek|kimi|gpt-oss)",
        _C(True, None, None, None, None, None, None, None, "bundled"),
    ),
    (
        r"^(gemma|phi|tinyllama|vicuna|orca)",
        _C(False, False, None, None, None, None, None, None, "bundled"),
    ),
)

_LITELLM_FIELD_MAP = {
    "supports_tools": "supports_function_calling",
    "supports_parallel_tools": "supports_parallel_function_calling",
    "supports_json_mode": "supports_response_schema",
    "supports_structured_output": "supports_response_schema",
    "supports_vision": "supports_vision",
    "supports_thinking": "supports_reasoning",
    "context_window": "max_input_tokens",
    "max_output_tokens": "max_output_tokens",
}

_CAP_FIELDS = tuple(f.name for f in fields(ModelCapabilities) if f.name != "source")

_litellm_cache: Dict[Tuple[str, str], Optional[ModelCapabilities]] = {}


def _normalise_model_id(model: str) -> str:
    """The bare model id used for family matching (drop a ``provider/`` prefix
    and a ``:tag`` Ollama suffix)."""
    bare = str(model or "").strip()
    if "/" in bare:
        bare = bare.rsplit("/", 1)[-1]
    if ":" in bare:
        bare = bare.split(":", 1)[0]
    return bare.lower()


def bundled_capabilities(model: str) -> Optional[ModelCapabilities]:
    bare = _normalise_model_id(model)
    if not bare:
        return None
    for pattern, caps in _BUNDLED:
        if re.match(pattern, bare):
            return ModelCapabilities(
                **{f: getattr(caps, f) for f in _CAP_FIELDS}, source="bundled"
            )
    return None


def litellm_capabilities(model: str, provider: str = "") -> Optional[ModelCapabilities]:
    """Capabilities from ``litellm.get_model_info``; ``None`` when LiteLLM is not
    installed or the model is not in its table. Cached per (model, provider)."""
    key = (str(model or ""), str(provider or ""))
    if key in _litellm_cache:
        return _litellm_cache[key]
    result: Optional[ModelCapabilities] = None
    try:
        import litellm  # optional extra

        custom = (provider or "").strip().lower()
        if custom in ("", "litellm") or "/" in str(model):
            info = litellm.get_model_info(model)
        else:
            info = litellm.get_model_info(model, custom_llm_provider=custom)
        values: Dict[str, Any] = {}
        for ours, theirs in _LITELLM_FIELD_MAP.items():
            raw = info.get(theirs) if isinstance(info, dict) else None
            if raw is None:
                continue
            values[ours] = (
                int(raw)
                if ours in ("context_window", "max_output_tokens")
                else bool(raw)
            )
        if values:
            result = ModelCapabilities(**values, source="litellm")
    except ImportError:
        result = None
    except Exception as exc:  # unmapped model, bad provider — not an error for us
        logger.debug(
            "capabilities: litellm has no entry for %r/%r: %s", provider, model, exc
        )
        result = None
    _litellm_cache[key] = result
    return result


def clear_capability_cache() -> None:
    _litellm_cache.clear()


def _merge(*layers: Optional[ModelCapabilities]) -> ModelCapabilities:
    """Field-wise merge: the first layer with a known value wins."""
    merged: Dict[str, Any] = {}
    sources = []
    for layer in layers:
        if layer is None:
            continue
        used = False
        for name in _CAP_FIELDS:
            if merged.get(name) is None and getattr(layer, name) is not None:
                merged[name] = getattr(layer, name)
                used = True
        if used and layer.source not in sources:
            sources.append(layer.source)
    return ModelCapabilities(**merged, source="+".join(sources) or "unknown")


def override_capabilities(overrides: Any) -> Optional[ModelCapabilities]:
    """An operator's ``model_capabilities`` dict (unknown keys ignored, values
    coerced) as a capabilities layer, or ``None`` when empty."""
    if not isinstance(overrides, dict) or not overrides:
        return None
    values: Dict[str, Any] = {}
    for name in _CAP_FIELDS:
        if name not in overrides or overrides[name] is None:
            continue
        raw = overrides[name]
        try:
            values[name] = (
                int(raw)
                if name in ("context_window", "max_output_tokens")
                else bool(raw)
            )
        except (TypeError, ValueError):
            logger.warning("capabilities: ignoring override %s=%r", name, raw)
    return ModelCapabilities(**values, source="override") if values else None


def resolve_capabilities(
    model: str,
    provider: str = "",
    overrides: Any = None,
) -> ModelCapabilities:
    """The effective capabilities for ``model`` (see module docstring for order)."""
    return _merge(
        override_capabilities(overrides),
        litellm_capabilities(model, provider),
        bundled_capabilities(model),
    )


__all__ = [
    "resolve_capabilities",
    "bundled_capabilities",
    "litellm_capabilities",
    "override_capabilities",
    "clear_capability_cache",
]
