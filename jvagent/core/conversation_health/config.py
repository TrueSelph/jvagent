"""Conversation Health configuration (app-level, enabled by default)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from jvagent.core.env_resolver import parse_bool_env


def _env_bool(name: str) -> Optional[bool]:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return None
    return parse_bool_env(raw)


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return parse_bool_env(value)
    return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ConversationHealthConfig:
    """Resolved Conversation Health knobs (app-level + env)."""

    enabled: bool = True
    flag_threshold: float = 70.0
    optimization_ceiling: float = 90.0
    reading_window_days: int = 7
    evidence_excerpt_max_chars: int = 120
    unflagged_ambient_max_rate: float = 0.05
    ambient_b_share: float = 0.5
    ambient_a_share: float = 0.5
    ambient_spillover: bool = True
    ambient_b_target_rate: float = 0.18
    ambient_a_target_rate: float = 0.02
    latency_band_low: float = 3.0
    latency_band_medium: float = 8.0
    latency_band_high: float = 15.0
    model_action_type: str = "OpenAILanguageModelAction"
    model: str = "gpt-4o-mini"
    model_temperature: float = 0.0
    model_max_tokens: int = 512
    enable_ai: bool = True
    history_limit: int = 6


def load_conversation_health_config(
    app_config: Optional[Dict[str, Any]] = None,
) -> ConversationHealthConfig:
    """Load config from app.yaml config.conversation_health and env overrides."""
    if app_config is None:
        try:
            from jvagent.core.config import load_app_config

            app_config = load_app_config()
        except Exception:
            app_config = {}

    section: Dict[str, Any] = {}
    if isinstance(app_config, dict):
        raw = app_config.get("conversation_health")
        if isinstance(raw, dict):
            section = raw

    env_enabled = _env_bool("JVAGENT_CONVERSATION_HEALTH_ENABLED")
    enabled = (
        env_enabled
        if env_enabled is not None
        else _as_bool(section.get("enabled"), True)
    )

    return ConversationHealthConfig(
        enabled=enabled,
        flag_threshold=_as_float(section.get("flag_threshold"), 70.0),
        optimization_ceiling=_as_float(section.get("optimization_ceiling"), 90.0),
        reading_window_days=_as_int(section.get("reading_window_days"), 7),
        evidence_excerpt_max_chars=_as_int(
            section.get("evidence_excerpt_max_chars"), 120
        ),
        unflagged_ambient_max_rate=_as_float(
            section.get("unflagged_ambient_max_rate"), 0.05
        ),
        ambient_b_share=_as_float(section.get("ambient_b_share"), 0.5),
        ambient_a_share=_as_float(section.get("ambient_a_share"), 0.5),
        ambient_spillover=_as_bool(section.get("ambient_spillover"), True),
        ambient_b_target_rate=_as_float(section.get("ambient_b_target_rate"), 0.18),
        ambient_a_target_rate=_as_float(section.get("ambient_a_target_rate"), 0.02),
        latency_band_low=_as_float(section.get("latency_band_low"), 3.0),
        latency_band_medium=_as_float(section.get("latency_band_medium"), 8.0),
        latency_band_high=_as_float(section.get("latency_band_high"), 15.0),
        model_action_type=str(
            section.get("model_action_type") or "OpenAILanguageModelAction"
        ),
        model=str(section.get("model") or "gpt-4o-mini"),
        model_temperature=_as_float(section.get("model_temperature"), 0.0),
        model_max_tokens=_as_int(section.get("model_max_tokens"), 512),
        enable_ai=_as_bool(section.get("enable_ai"), True),
        history_limit=_as_int(section.get("history_limit"), 6),
    )


def is_enabled_for_agent(
    agent: Any,
    config: Optional[ConversationHealthConfig] = None,
) -> bool:
    """App default on, optional agent.conversation_health_enabled override."""
    cfg = config or load_conversation_health_config()
    if not cfg.enabled:
        return False
    override = getattr(agent, "conversation_health_enabled", None)
    if override is None:
        return True
    return _as_bool(override, True)
