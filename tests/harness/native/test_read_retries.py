"""Read-only receipt repair must never settle writes or cancelled work."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai import ModelRetry
from pydantic_ai_harness import StepPersistence
from pydantic_ai_harness.step_persistence import ToolEffectRecord

from jvagent.harness.read_retries import ReadRetryReceipts


@pytest.mark.asyncio
@pytest.mark.parametrize("exhausted", [False, True])
@pytest.mark.parametrize("rejected_id", ["example", "missing", "../../outside"])
async def test_rejected_sdk_skill_load_has_terminal_failure_receipts(
    tmp_path: Path, exhausted: bool, rejected_id: str
) -> None:
    from pydantic_ai.exceptions import UnexpectedModelBehavior
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    from jvagent.harness.native import build_native_agent
    from jvagent.harness.step_store import InteractionStepStore

    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: example\ndescription: Example instructions\n---\nHelp the user.\n"
    )

    async def save() -> None:
        pass

    async def access(name: str) -> bool:
        return True

    store = InteractionStepStore(SimpleNamespace(native_steps={}, save=save))
    step = 0

    def respond(messages: Any, info: Any) -> ModelResponse:
        nonlocal step
        step += 1
        if step <= (3 if exhausted else 2):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "load_capability",
                        {"id": "example" if step == 1 else rejected_id},
                        tool_call_id=f"load-{step}",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("done")])

    agent = build_native_agent(
        FunctionModel(respond),
        skill_directories=[skill],
        tools=[],
        access_check=access,
        instructions="Use the example skill.",
        step_store=store,
    )
    if exhausted:
        with pytest.raises(UnexpectedModelBehavior, match="exceeded max retries"):
            await agent.run("Help", run_id="load-run", conversation_id="session")
    else:
        assert (
            await agent.run("Help", run_id="load-run", conversation_id="session")
        ).output == "done"
    effects = [
        await store.get_tool_effect(run_id="load-run", tool_call_id=f"load-{n}")
        for n in range(1, (3 if exhausted else 2) + 1)
    ]
    assert {effect.tool_call_id: effect.status for effect in effects} == {
        f"load-{number}": "completed" if number == 1 else "failed"
        for number in range(1, (3 if exhausted else 2) + 1)
    }
    assert not await store.list_unresolved_tool_effects(run_id="load-run")


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["read", "load_capability"])
@pytest.mark.parametrize("scenario", ["write", "cancel", "save_failure"])
async def test_uncertain_effects_and_failed_receipts_are_not_hidden(
    scenario: str,
    name: str,
) -> None:
    store = SimpleNamespace(
        get_tool_effect=AsyncMock(
            return_value=ToolEffectRecord(
                run_id="run", tool_call_id="call", tool_name=name, status="started"
            )
        ),
        record_tool_effect=AsyncMock(
            side_effect=(
                RuntimeError("storage unavailable")
                if scenario == "save_failure"
                else None
            )
        ),
    )
    capability = ReadRetryReceipts(StepPersistence(store=store), frozenset({name}))

    async def handler(args: Any) -> Any:
        if scenario == "cancel":
            raise asyncio.CancelledError()
        raise ModelRetry("Try another argument")

    expected = (
        asyncio.CancelledError
        if scenario == "cancel"
        else RuntimeError if scenario == "save_failure" else ModelRetry
    )
    with pytest.raises(expected):
        await capability.wrap_tool_execute(
            SimpleNamespace(run_id="run"),
            call=SimpleNamespace(tool_call_id="call"),
            tool_def=SimpleNamespace(name="write" if scenario == "write" else name),
            args={},
            handler=handler,
        )
    if scenario != "save_failure":
        store.record_tool_effect.assert_not_awaited()
