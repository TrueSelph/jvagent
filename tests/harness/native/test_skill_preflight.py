"""Configured root and child skills fail before any paid model request."""

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from jvagent.harness.native import build_native_agent, run_native_agent


@pytest.mark.asyncio
@pytest.mark.parametrize("child", [False, True])
@pytest.mark.parametrize("invalid_name", [False, True])
async def test_unloadable_skill_is_rejected_before_model_request(
    tmp_path: Path, child: bool, invalid_name: bool
) -> None:
    directory = tmp_path / ("invalid_name" if invalid_name else "disabled")
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        f"---\nname: {directory.name}\ndescription: Test admission\n"
        + ("" if invalid_name else "disable-model-invocation: true\n")
        + "---\nAnswer briefly.\n"
    )
    calls = []

    def respond(messages: Any, info: Any) -> ModelResponse:
        calls.append(True)
        return ModelResponse(parts=[TextPart("should never be requested")])

    async def access(label: str) -> bool:
        return True

    agent = build_native_agent(
        FunctionModel(respond),
        skill_directory=Path("jvagent/skills/delegate") if child else directory,
        tools=[],
        access_check=access,
        instructions="Follow your selected skill.",
        conversation_id="host-conversation",
        delegates=(
            [
                {
                    "name": "worker",
                    "description": "Check admission",
                    "skill_directory": str(directory),
                }
            ]
            if child
            else []
        ),
    )
    with pytest.raises(ValueError, match="Configured skill is not model-invocable"):
        await run_native_agent(
            agent,
            "Delegate the task",
            conversation_id="host-conversation",
            run_id="run",
        )
    assert calls == []
