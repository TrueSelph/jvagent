"""Regression: Action.export() must redact secrets like api_key."""

from __future__ import annotations

import pytest

from jvagent.action.model.language.openai.openai import OpenAILanguageModelAction


@pytest.mark.asyncio
async def test_api_key_redacted_in_nested_export() -> None:
    action = OpenAILanguageModelAction(
        api_key="sk-test-DO-NOT-LEAK",
        api_endpoint="https://api.openai.com/v1",
        model="gpt-4o-mini",
        namespace="test",
        label="openai_lm",
    )
    data = await action.export()
    context = data.get("context", {})
    assert "api_key" not in context, "api_key must not appear in nested export context"
    assert "sk-test-DO-NOT-LEAK" not in str(data), "secret value leaked in export dump"


@pytest.mark.asyncio
async def test_api_key_redacted_in_flat_export() -> None:
    action = OpenAILanguageModelAction(
        api_key="sk-test-DO-NOT-LEAK",
        api_endpoint="https://api.openai.com/v1",
        model="gpt-4o-mini",
        namespace="test",
        label="openai_lm",
    )
    data = await action.export(flat=True)
    assert "api_key" not in data, "api_key must not appear in flat export"
    assert "sk-test-DO-NOT-LEAK" not in str(data), "secret value leaked in flat export"
