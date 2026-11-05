"""Domain scheduling invokes the same SDK composition and persists its outcome."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from jvagent.action.native_harness import NativeHarnessAction
from jvagent.action.reply import ReplyAction
from jvagent.action.response.response_bus import ResponseBus
from jvagent.action.task_monitor.task_monitor import TaskMonitor
from jvagent.memory.conversation import Conversation
from jvagent.memory.task_proactive import ProactiveTaskSpec
from jvagent.memory.task_store import TaskStore


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True, "unqualified_pin", "cancel"])
async def test_native_task_sdk_output_and_failure_do_not_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: bool | str,
) -> None:
    from jvagent.action.native_harness import native_harness_action as module

    for name in ("conversation", "summary"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Prepare summaries\n---\nReturn the requested summary.\n"
        )
    conversation = await Conversation.create(
        user_id="caller", session_id="proactive-session"
    )
    store = TaskStore(conversation)
    handle = await store.enqueue_proactive(
        ProactiveTaskSpec(
            directive="Prepare a summary of ALDER-42",
            context="Synthetic fixture",
            skill="summary",
            pinned_tools=["unqualified_tool"] if failure == "unqualified_pin" else [],
        )
    )
    native = NativeHarnessAction()
    bus = ResponseBus()
    bus.native_execution = True
    agent = SimpleNamespace(
        id="agent",
        get_actions=AsyncMock(return_value=[native]),
        get_action_by_type=AsyncMock(return_value=None),
        get_response_bus=AsyncMock(return_value=bus),
    )
    seen = []

    def model(messages: Any, info: Any) -> ModelResponse:
        prompts = [
            part.content
            for message in messages
            for part in message.parts
            if isinstance(part, UserPromptPart)
        ]
        seen.append("\n".join(prompts))
        if failure == "cancel":
            raise asyncio.CancelledError()
        if failure:
            raise RuntimeError("Synthetic provider failure")
        return ModelResponse(parts=[TextPart("Summary ALDER-42")])

    original = module.build_native_agent

    async def stream(messages: Any, info: Any) -> AsyncIterator[str]:
        yield model(messages, info).parts[0].content

    def factory(*args: Any, **kwargs: Any) -> Any:
        assert kwargs["skill_directory"] == tmp_path / "summary"
        return original(
            FunctionModel(model, stream_function=stream, model_name="gpt-4.1"), **kwargs
        )

    monkeypatch.setattr(module, "build_native_agent", factory)
    walker_ref = []

    facade = SimpleNamespace(
        id=native.id,
        get_agent=AsyncMock(return_value=agent),
        get_responder=AsyncMock(return_value=ReplyAction()),
        model="openai:gpt-4.1",
        model_settings={},
        skill_directory=str(tmp_path / "conversation"),
        delegates=[],
        instructions="Follow host policy.",
        context_max_tokens=32000,
        context_keep_tokens=16000,
        max_run_cost_usd="2",
        max_conversation_cost_usd="20",
        max_requests=3,
        max_tokens=10000,
        timeout_seconds=10,
    )

    class Walker:
        def __init__(self, **kwargs: Any) -> None:
            self.__dict__.update(kwargs)
            self._agent = agent
            self._host_system_context = None
            self.correlation_id = "proactive-test-run"
            walker_ref.append(self)

        async def spawn(self, owner: Any) -> None:
            self.interaction = await self.conversation.create_interaction(
                utterance="", session_id=self.session_id
            )
            await NativeHarnessAction.execute(facade, self)

    monkeypatch.setattr(
        "jvagent.action.interact.interact_walker.InteractWalker", Walker
    )
    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await TaskMonitor(agent_id=agent.id).dispatch_one(
                agent, conversation, handle, store=store
            )
        result = False
    else:
        result = await TaskMonitor(agent_id=agent.id).dispatch_one(
            agent, conversation, handle, store=store
        )
    task = TaskStore(conversation).get(handle.id)
    if failure != "unqualified_pin":
        assert "agent-initiated" in seen[0] and "ALDER-42" in seen[0], seen
    assert result is (not failure)
    assert task.status == ("failed" if failure else "completed")
    assert task.data["attempt_count"] == 0
    assert len(seen) == (0 if failure == "unqualified_pin" else 1)
    interaction = walker_ref[0].interaction
    assert interaction.utterance == ""
    assert interaction.native_run["status"] == (
        "blocked"
        if failure == "unqualified_pin"
        else (
            "cancelled" if failure == "cancel" else "failed" if failure else "delivered"
        )
    )
    assert bool(interaction.native_steps.get("events")) is (
        failure != "unqualified_pin"
    )
    assert not await TaskMonitor(agent_id=agent.id).dispatch_one(
        agent, conversation, task, store=TaskStore(conversation)
    )
    assert len(seen) == (0 if failure == "unqualified_pin" else 1)
