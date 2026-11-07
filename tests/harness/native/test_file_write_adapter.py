"""Native writes require SDK journaling, named access and durable local readback."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pydantic_ai_harness")
from jvspatial.storage.interfaces.local import LocalFileInterface
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from jvagent.action.access_control import AccessControlAction
from jvagent.action.file_interface import FileInterfaceAction, _core
from jvagent.core.sandbox import resolve_user_sandbox_relpath
from jvagent.harness.native import build_native_agent
from jvagent.harness.recovery import NativeHistoryError, history_for_new_turn
from jvagent.harness.step_store import InteractionStepStore
from jvagent.memory.conversation import Conversation
from jvagent.memory.interaction import Interaction
from jvagent.tooling.access import is_tool_allowed
from jvagent.tooling.context import bind_dispatch_context


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["absent", "unnamed", "denied", "allowed"])
async def test_native_file_write_requires_named_permission_before_io(
    tmp_path: Path,
    monkeypatch: Any,
    policy: str,
) -> None:
    await execute(tmp_path, monkeypatch, policy=policy)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["after-write", "readback", "provider", "collision"]
)
async def test_failed_write_run_never_discards_mutation_and_replays_input(
    tmp_path: Path,
    monkeypatch: Any,
    failure: str,
) -> None:
    await execute(tmp_path, monkeypatch, policy="allowed", failure=failure)


async def execute(
    tmp_path: Path,
    monkeypatch: Any,
    *,
    policy: str,
    failure: str = "",
) -> None:
    conversation = Conversation(user_id="caller", session_id="session")
    await conversation.save()
    interaction = Interaction(
        conversation_id=conversation.id,
        user_id="caller",
        session_id="session",
        utterance="Write the note",
    )
    await interaction.save()
    interaction.native_run = {"run_id": "write-run", "status": "running"}
    store = InteractionStepStore(interaction)
    caller = SimpleNamespace(
        _agent=SimpleNamespace(id="agent"),
        user_id="caller",
        session_id="session",
        channel="default",
        conversation=conversation,
        interaction=interaction,
    )
    storage = LocalFileInterface(root_dir=str(tmp_path / "files"))

    async def get_storage(visitor: Any) -> Any:
        return storage

    monkeypatch.setattr(_core, "_get_file_interface", get_storage)
    original_write = _core.write_bounded_text_file
    bodies: list[str] = []

    async def write(*args: Any, **kwargs: Any) -> None:
        effects = interaction.native_steps["effects"]
        assert effects[-1]["status"] == "started"
        assert effects[-1]["effect_summary"] == "jv:mutation:file-write:v1"
        assert effects[-1]["idempotency_key"]
        bodies.append("write")
        await original_write(*args, **kwargs)
        if failure == "after-write":
            raise RuntimeError("failure after physical write")

    monkeypatch.setattr(_core, "write_bounded_text_file", write)
    if failure == "readback":
        monkeypatch.setattr(
            _core, "read_bounded_text_file", AsyncMock(return_value="unexpected")
        )
    permissions: dict[str, Any] = {"default": {"any": {"allow": [{"group": "all"}]}}}
    if policy in {"allowed", "denied"}:
        permissions["default"]["tools"] = {
            "file_interface__write_file": {
                "allow": [{"user": "caller"}] if policy == "allowed" else [],
                "deny": [],
            }
        }
    ac = None if policy == "absent" else AccessControlAction(permissions=permissions)
    agent_host = SimpleNamespace(get_access_control_action=AsyncMock(return_value=ac))

    async def access(name: str) -> bool:
        return True

    async def tool_access(tool: Any) -> bool:
        return await is_tool_allowed(
            agent_host,
            label=tool.name,
            tool_name=tool.name,
            requires_tool_permission=tool.requires_tool_permission,
            user_id="caller",
            channel="default",
        )

    skill = tmp_path / "file-write"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: file-write\ndescription: Write note\nallowed-tools: [file_interface__write_file]\n---\nWrite the requested note.\n"
    )
    requests = 0

    def respond(messages: Any, info: Any) -> ModelResponse:
        nonlocal requests
        requests += 1
        if requests == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "file_interface__write_file",
                        {"path": "note.txt", "content": "OWNED_NOTE"},
                        tool_call_id="write-call",
                    )
                ]
            )
        if failure == "provider":
            raise RuntimeError("provider failed after write")
        if failure == "collision" and requests == 2:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "file_interface__write_file",
                        {"path": "note.txt", "content": "REPEATED_WRITE"},
                        tool_call_id="write-call",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("done")])

    agent = build_native_agent(
        FunctionModel(respond),
        skill_directory=skill,
        tools=await FileInterfaceAction().get_native_tools(),
        access_check=access,
        tool_access_check=tool_access,
        step_store=store,
        instructions="Write",
        conversation_id="session",
    )
    with bind_dispatch_context(caller):
        if policy != "allowed" or failure:
            with pytest.raises((PermissionError, RuntimeError, ValueError)):
                await agent.run(
                    interaction.utterance, run_id="write-run", conversation_id="session"
                )
        else:
            await agent.run(
                interaction.utterance, run_id="write-run", conversation_id="session"
            )
    path = (
        storage.root_dir / resolve_user_sandbox_relpath("agent", "caller") / "note.txt"
    )
    assert bodies == (["write"] if policy == "allowed" else [])
    assert path.exists() == (policy == "allowed")
    effect = await store.get_tool_effect(run_id="write-run", tool_call_id="write-call")
    if policy == "allowed":
        assert path.read_text() == "OWNED_NOTE"
        assert effect.effect_summary == "jv:mutation:file-write:v1"
        assert effect.status == (
            "failed" if failure in {"after-write", "readback"} else "completed"
        )
    else:
        assert effect.effect_summary != "jv:mutation:file-write:v1"
    if failure:
        interaction.native_run = {"run_id": "write-run", "status": "failed"}
        with pytest.raises(
            NativeHistoryError, match="mutation requires reconciliation"
        ):
            await history_for_new_turn(
                interaction,
                conversation_id=conversation.id,
                user_id="caller",
                session_id="session",
            )
        assert path.read_text() == "OWNED_NOTE" and bodies == ["write"]
