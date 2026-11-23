"""Native model configuration uses the SDK schema and request settings."""

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from jvagent.harness.native import build_native_agent


@pytest.fixture
def skill(tmp_path: Path) -> Path:
    directory = tmp_path / "conversation"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: conversation\ndescription: Converse\nallowed-tools: []\n---\nAnswer briefly.\n"
    )
    return directory


@pytest.mark.asyncio
async def test_declared_settings_reach_the_native_model_unchanged(skill: Path) -> None:
    configured = {"temperature": 0.0, "max_tokens": 8192, "max_retries": 0}
    seen = []

    def respond(messages: Any, info: Any) -> ModelResponse:
        seen.append(dict(info.model_settings))
        return ModelResponse(parts=[TextPart("ok")])

    async def access(label: str) -> bool:
        return True

    agent = build_native_agent(
        FunctionModel(respond),
        skill_directory=skill,
        tools=[],
        access_check=access,
        instructions="Answer briefly.",
        model_settings=configured,
    )
    await agent.run("hello")
    assert seen == [configured]
    assert configured == {"temperature": 0.0, "max_tokens": 8192, "max_retries": 0}


@pytest.mark.parametrize(
    "settings",
    [
        {"temprature": 0},
        {"max_tokens": True},
        {"temperature": "zero"},
        {"temperature": float("nan")},
    ],
)
def test_invalid_settings_fail_during_configuration(
    settings: dict[str, Any], skill: Path
) -> None:
    async def access(label: str) -> bool:
        return True

    with pytest.raises(ValueError):
        build_native_agent(
            FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("ok")])),
            skill_directory=skill,
            tools=[],
            access_check=access,
            instructions="Answer briefly.",
            model_settings=settings,
        )


def test_invalid_settings_error_does_not_echo_values() -> None:
    from jvagent.harness.model_settings import validate_model_settings

    with pytest.raises(ValueError) as error:
        validate_model_settings({"api_key": "synthetic-private-setting"})
    assert "synthetic-private-setting" not in str(error.value)


@pytest.mark.asyncio
async def test_native_action_rejects_invalid_settings_during_startup() -> None:
    from jvagent.action.native_harness import NativeHarnessAction

    action = NativeHarnessAction(model_settings={"temprature": 0.0})
    with pytest.raises(ValueError, match="Invalid native model settings"):
        await action.on_startup()
