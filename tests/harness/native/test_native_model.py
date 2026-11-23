"""Provider overrides stay narrow and use the SDK's public model profile."""

from typing import Any

import pytest

pytest.importorskip("pydantic_ai_harness")

from jvagent.harness.native_model import qualified_model


@pytest.mark.parametrize("prefix", ["openai", "openai-responses"])
@pytest.mark.parametrize("name", ["gpt-4.1", "gpt-4.1-2025-04-14"])
def test_gpt41_uses_public_standard_tool_projection(
    monkeypatch: pytest.MonkeyPatch, prefix: str, name: str
) -> None:
    from pydantic_ai.models.openai import OpenAIResponsesModel

    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")
    model = qualified_model(f"{prefix}:{name}")
    assert isinstance(model, OpenAIResponsesModel)
    assert model.model_name == name
    assert model.tool_addition_mode is None


@pytest.mark.parametrize(
    "model",
    [
        "openai:gpt-4.1-mini",
        "openai:gpt-4.1-nano",
        "openai:gpt-5",
        "openai-chat:gpt-4.1",
        "ollama:example",
        object(),
    ],
)
def test_other_declarations_and_explicit_instances_remain_unchanged(model: Any) -> None:
    assert qualified_model(model) is model
