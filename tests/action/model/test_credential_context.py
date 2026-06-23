"""Per-turn model credential override (BYOK multi-tenant)."""

from __future__ import annotations

import asyncio

import pytest

from jvagent.action.model.base import BaseModelAction
from jvagent.action.model.context import (
    bind_model_gear,
    bind_model_override,
    get_model_override,
    model_action_class_for_provider,
    reset_model_override,
    set_calling_action_name,
    set_model_override,
)


class _StubModelAction(BaseModelAction):
    pass


def test_model_action_class_for_provider():
    assert model_action_class_for_provider("openai") == "OpenAILanguageModelAction"
    assert (
        model_action_class_for_provider("anthropic") == "AnthropicLanguageModelAction"
    )
    assert model_action_class_for_provider("unknown") is None


def test_api_key_from_context_uses_override(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    action = _StubModelAction()
    token = set_model_override(
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "user-byok-key",
        }
    )
    try:
        assert action.api_key_from_context("OPENAI_API_KEY") == "user-byok-key"
        assert action.api_key_from_context("ANTHROPIC_API_KEY") == "user-byok-key"
    finally:
        reset_model_override(token)


def test_api_key_from_context_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    action = _StubModelAction()
    assert action.api_key_from_context("OPENAI_API_KEY") == "env-key"
    assert get_model_override() is None


def test_bind_model_override_restores_prior(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    action = _StubModelAction()
    outer = set_model_override({"provider": "openai", "model": "m1", "api_key": "k1"})
    try:
        with bind_model_override(
            {"provider": "anthropic", "model": "m2", "api_key": "k2"}
        ):
            assert action.api_key_from_context("OPENAI_API_KEY") == "k2"
        assert action.api_key_from_context("OPENAI_API_KEY") == "k1"
    finally:
        reset_model_override(outer)


@pytest.mark.asyncio
async def test_concurrency_isolation_no_key_bleed(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    action = _StubModelAction()
    barrier = asyncio.Barrier(2)
    results: list[str] = []

    async def run_turn(key: str) -> None:
        with bind_model_override(
            {"provider": "openai", "model": "gpt-4o-mini", "api_key": key}
        ):
            await barrier.wait()
            await asyncio.sleep(0.01)
            results.append(action.api_key_from_context("OPENAI_API_KEY"))

    await asyncio.gather(run_turn("tenant-a"), run_turn("tenant-b"))
    assert sorted(results) == ["tenant-a", "tenant-b"]


def test_api_key_from_context_uses_light_key_on_light_gear(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-anthropic")
    action = _StubModelAction()
    override = {
        "slots": {
            "default": {
                "provider": "openai",
                "model": "o3-mini",
                "api_key": "sk-openai-heavy",
            },
            "light": {
                "provider": "anthropic",
                "model": "claude-3-5-haiku-latest",
                "api_key": "sk-ant-light",
            },
        },
    }
    with bind_model_override(override), bind_model_gear("light"):
        set_calling_action_name("OrchestratorInteractAction")
        assert action.api_key_from_context("ANTHROPIC_API_KEY") == "sk-ant-light"
    with bind_model_override(override), bind_model_gear("heavy"):
        set_calling_action_name("OrchestratorInteractAction")
        assert action.api_key_from_context("OPENAI_API_KEY") == "sk-openai-heavy"


def test_normalize_legacy_override_to_slots():
    from jvagent.action.model.context import normalize_model_override

    raw = {
        "provider": "openai",
        "model": "gpt-4.1",
        "api_key": "sk-main",
        "light_model": "gpt-4o-mini",
        "light_provider": "anthropic",
        "light_api_key": "sk-light",
    }
    normalized = normalize_model_override(raw)
    assert normalized is not None
    slots = normalized["slots"]
    assert slots["default"]["model"] == "gpt-4.1"
    assert slots["light"]["model"] == "gpt-4o-mini"
    assert slots["light"]["api_key"] == "sk-light"


def test_resolve_slot_config_falls_back_to_default():
    from jvagent.action.model.context import resolve_slot_config

    override = {
        "slots": {
            "default": {
                "provider": "openai",
                "model": "gpt-4.1",
                "api_key": "sk-default",
            },
        },
    }
    cfg = resolve_slot_config(
        "vision",
        calling_action_name="VisionAction",
        override=override,
    )
    assert cfg is not None
    assert cfg["model"] == "gpt-4.1"
    assert cfg["api_key"] == "sk-default"
