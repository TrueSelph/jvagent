"""Context variables for model action observability and per-turn overrides."""

from __future__ import annotations

import contextlib
import contextvars
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from jvagent.memory.interaction import Interaction

# Maps BYOK provider slug to LanguageModelAction class name on the agent.
PROVIDER_MODEL_ACTION_CLASSES: Dict[str, str] = {
    "openai": "OpenAILanguageModelAction",
    "anthropic": "AnthropicLanguageModelAction",
    "ollama": "OllamaLanguageModelAction",
    "openrouter": "OpenRouterLanguageModelAction",
    "groq": "GroqLanguageModelAction",
}

# Canonical model slots for resident-harness BYOK (Integral and other hosts).
# ``default`` is required; others are optional and fall back to default.
MODEL_SLOTS: Tuple[str, ...] = ("default", "light", "heavy", "vision")

# Fixed jvagent action → slot binding (harness contract).
ACTION_SLOT_FOR_GEAR: Dict[str, Dict[str, str]] = {
    "OrchestratorInteractAction": {"light": "light", "heavy": "heavy"},
}
ACTION_SLOT_DEFAULT: Dict[str, str] = {
    "OrchestratorInteractAction": "default",
    "ReplyAction": "default",
    "VisionAction": "vision",
}

# Per-turn credential + model override for multi-tenant hosts (e.g. Integral BYOK).
# Canonical shape: {"slots": {slot: {provider, model, api_key?}, ...}}
# Legacy flat keys (provider, model, api_key, light_model?, …) are normalized on read.
# Plaintext keys live only in this ContextVar for one async task; never persist.
per_turn_model_override: contextvars.ContextVar[Optional[Dict[str, Any]]] = (
    contextvars.ContextVar("per_turn_model_override", default=None)
)

# Active model gear for the in-flight orchestrator call ("light" | "heavy").
per_turn_model_gear: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "per_turn_model_gear", default=None
)

# Context variable to track current interaction for observability
current_interaction: contextvars.ContextVar[Optional[Any]] = contextvars.ContextVar(
    "current_interaction", default=None
)

# Context variable to track the calling action name for observability
current_action_name: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_action_name", default=None
)


def get_interaction() -> Optional[Any]:
    """Get the current interaction object from context.

    Returns:
        Interaction object if set in context, None otherwise
    """
    return current_interaction.get()


def set_interaction(interaction: Optional[Any]) -> None:
    """Set the current interaction object in context.

    Args:
        interaction: Interaction object to set
    """
    current_interaction.set(interaction)


def get_calling_action_name() -> Optional[str]:
    """Get the current calling action name from context.

    Returns:
        Calling action name (camelCase class name) if set in context, None otherwise
    """
    return current_action_name.get()


def set_calling_action_name(action_name: Optional[str]) -> None:
    """Set the current calling action name in context.

    Args:
        action_name: Action name (camelCase class name) to set (e.g., "PersonaAction", "ExampleInteractAction")
    """
    current_action_name.set(action_name)


def get_model_override() -> Optional[Dict[str, Any]]:
    """Return the per-turn model credential override, if any."""
    return per_turn_model_override.get()


def set_model_override(override: Optional[Dict[str, Any]]) -> contextvars.Token:
    """Install a per-turn override dict; returns a reset token."""
    return per_turn_model_override.set(override)


def reset_model_override(token: contextvars.Token) -> None:
    """Restore the prior override slot."""
    per_turn_model_override.reset(token)


@contextlib.contextmanager
def bind_model_override(override: Optional[Dict[str, Any]]):
    """Context manager for per-turn BYOK credential + model selection."""
    token = set_model_override(override)
    try:
        yield
    finally:
        reset_model_override(token)


def get_model_gear() -> Optional[str]:
    """Return the active orchestrator gear for this turn, if any."""
    return per_turn_model_gear.get()


def set_model_gear(gear: Optional[str]) -> contextvars.Token:
    """Install light/heavy gear for api_key resolution on the current call."""
    return per_turn_model_gear.set(gear)


def reset_model_gear(token: contextvars.Token) -> None:
    """Restore the prior gear slot."""
    per_turn_model_gear.reset(token)


@contextlib.contextmanager
def bind_model_gear(gear: Optional[str]):
    """Context manager for gear-scoped BYOK key selection."""
    token = set_model_gear(gear)
    try:
        yield
    finally:
        reset_model_gear(token)


def model_action_class_for_provider(provider: str) -> Optional[str]:
    """Resolve a provider slug to a LanguageModelAction class name."""
    return PROVIDER_MODEL_ACTION_CLASSES.get((provider or "").strip().lower())


def _slot_entry(
    provider: str,
    model: str,
    api_key: str = "",
) -> Dict[str, str]:
    return {
        "provider": (provider or "").strip(),
        "model": (model or "").strip(),
        "api_key": (api_key or "").strip(),
    }


def normalize_model_override(
    override: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Return override with a canonical ``slots`` map (legacy flat keys merged in)."""
    if not override:
        return None
    if override.get("slots"):
        return override

    provider = str(override.get("provider") or "").strip()
    model = str(override.get("model") or "").strip()
    api_key = str(override.get("api_key") or "").strip()
    if not provider and not model:
        return override

    slots: Dict[str, Dict[str, str]] = {}
    if model:
        slots["default"] = _slot_entry(provider, model, api_key)

    light_model = str(override.get("light_model") or "").strip()
    if light_model:
        light_provider = str(override.get("light_provider") or provider or "").strip()
        light_key = str(override.get("light_api_key") or api_key or "").strip()
        slots["light"] = _slot_entry(light_provider, light_model, light_key)

    heavy_model = str(override.get("heavy_model") or "").strip()
    if heavy_model:
        heavy_provider = str(override.get("heavy_provider") or provider or "").strip()
        heavy_key = str(override.get("heavy_api_key") or api_key or "").strip()
        slots["heavy"] = _slot_entry(heavy_provider, heavy_model, heavy_key)

    vision_model = str(override.get("vision_model") or "").strip()
    if vision_model:
        vision_provider = str(override.get("vision_provider") or provider or "").strip()
        vision_key = str(override.get("vision_api_key") or api_key or "").strip()
        slots["vision"] = _slot_entry(vision_provider, vision_model, vision_key)

    if not slots:
        return override
    merged = dict(override)
    merged["slots"] = slots
    return merged


def resolve_slot_name(
    slot: str,
    *,
    calling_action_name: Optional[str] = None,
    gear: Optional[str] = None,
) -> str:
    """Map an action + gear to a slot name (before fallback)."""
    action = (calling_action_name or get_calling_action_name() or "").strip()
    gear_key = (gear if gear is not None else get_model_gear() or "").strip().lower()
    by_gear = ACTION_SLOT_FOR_GEAR.get(action)
    if by_gear and gear_key in by_gear:
        return by_gear[gear_key]
    bound = ACTION_SLOT_DEFAULT.get(action)
    if bound:
        return bound
    return (slot or "default").strip().lower() or "default"


def resolve_slot_config(
    slot: str,
    *,
    calling_action_name: Optional[str] = None,
    gear: Optional[str] = None,
    override: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, str]]:
    """Resolve {provider, model, api_key} for a slot with default fallback."""
    raw = override if override is not None else get_model_override()
    normalized = normalize_model_override(raw)
    if not normalized:
        return None
    slots: Dict[str, Dict[str, str]] = normalized.get("slots") or {}
    if not slots:
        return None

    slot_name = resolve_slot_name(
        slot, calling_action_name=calling_action_name, gear=gear
    )
    entry = slots.get(slot_name)
    if entry and str(entry.get("model") or "").strip():
        resolved = dict(entry)
        if not str(resolved.get("api_key") or "").strip():
            default_entry = slots.get("default") or {}
            if (
                str(default_entry.get("provider") or "").strip()
                == str(resolved.get("provider") or "").strip()
            ):
                resolved["api_key"] = str(default_entry.get("api_key") or "").strip()
        return resolved

    if slot_name != "default":
        default_entry = slots.get("default")
        if default_entry and str(default_entry.get("model") or "").strip():
            return dict(default_entry)
    return None
