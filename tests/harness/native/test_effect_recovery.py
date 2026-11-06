"""Operator verification consumes SDK results without replaying a mutation."""

import asyncio
import copy
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pydantic_ai_harness")
from jvspatial.storage.interfaces.local import LocalFileInterface
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage
from pydantic_ai_harness.step_persistence import is_provider_valid

from jvagent.action.file_interface import FileInterfaceAction, _core
from jvagent.core.sandbox import resolve_user_sandbox_relpath
from jvagent.harness import effect_recovery
from jvagent.harness.native import build_native_agent, run_native_agent
from jvagent.harness.recovery import NativeHistoryError, history_for_new_turn
from jvagent.harness.step_store import InteractionStepStore
from jvagent.memory.conversation import Conversation
from jvagent.memory.interaction import Interaction
from jvagent.tooling.context import bind_dispatch_context


async def interrupted(tmp_path: Path, monkeypatch: Any) -> Any:
    conversation = Conversation(user_id="caller", session_id="session")
    await conversation.save()
    interaction = Interaction(
        conversation_id=conversation.id,
        user_id="caller",
        session_id="session",
        utterance="Write the note",
    )
    interaction.native_run = {"run_id": "interrupted", "status": "running"}
    await interaction.save()
    storage = LocalFileInterface(root_dir=str(tmp_path / "files"))
    monkeypatch.setattr(_core, "_get_file_interface", AsyncMock(return_value=storage))
    action = FileInterfaceAction(agent_id="agent")
    host = SimpleNamespace(id="agent", get_actions=AsyncMock(return_value=[action]))
    monkeypatch.setattr(Conversation, "get_agent", AsyncMock(return_value=host))
    visitor = SimpleNamespace(
        _agent=host,
        user_id="caller",
        session_id="session",
        channel="default",
        conversation=conversation,
        interaction=interaction,
    )
    skill = tmp_path / "write"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: write\ndescription: Write\nallowed-tools: [file_interface__write_file]\n---\nWrite the requested file.\n"
    )
    bodies = []
    original = _core.write_bounded_text_file

    async def write(*args: Any, **kwargs: Any) -> None:
        bodies.append(1)
        await original(*args, **kwargs)
        raise asyncio.CancelledError()

    monkeypatch.setattr(_core, "write_bounded_text_file", write)

    def respond(messages: Any, info: Any) -> ModelResponse:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "file_interface__write_file",
                    {"path": "note.txt", "content": "VERIFIED"},
                    tool_call_id="write-call",
                )
            ],
            usage=RequestUsage(
                input_tokens=10, output_tokens=10, cost=Decimal("0.001")
            ),
        )

    async def allow(name: Any) -> bool:
        return True

    agent = build_native_agent(
        FunctionModel(respond, model_name="gpt-4.1"),
        skill_directory=skill,
        tools=await action.get_native_tools(),
        access_check=allow,
        tool_access_check=allow,
        step_store=InteractionStepStore(interaction, agent_id="agent"),
        instructions="Write",
        conversation_id="session",
    )
    with bind_dispatch_context(visitor), pytest.raises(asyncio.CancelledError):
        await agent.run("Write", run_id="interrupted", conversation_id="session")
    # A real process-death gate is separately covered in core tests/browser.
    monkeypatch.setattr(
        effect_recovery, "require_dead_native_owner", lambda metadata: None
    )
    path = (
        storage.root_dir / resolve_user_sandbox_relpath("agent", "caller") / "note.txt"
    )
    assert path.read_text() == "VERIFIED" and bodies == [1]
    return SimpleNamespace(
        conversation=conversation,
        interaction=interaction,
        action=action,
        skill=skill,
        bodies=bodies,
        path=path,
        allow=allow,
    )


@pytest.mark.asyncio
async def test_verified_ack_continues_read_only_and_preserves_sdk_facts(
    tmp_path: Path, monkeypatch: Any
) -> None:
    fixture = await interrupted(tmp_path, monkeypatch)
    original = copy.deepcopy(fixture.interaction.native_steps)
    inspected = await effect_recovery.inspect_effect_recovery(
        fixture.interaction, fixture.conversation
    )
    approval = dict(
        digest=inspected.facts["digest"],
        actor="admin",
        evidence="Verified local file",
        reason="Continue without replay",
    )
    receipt = await effect_recovery.approve_effect_recovery(
        fixture.interaction, fixture.conversation, **approval
    )
    assert receipt == await effect_recovery.approve_effect_recovery(
        fixture.interaction, fixture.conversation, **approval
    )
    from jvagent.harness.recovery import RECOVERY_NOTICE

    notice = SimpleNamespace(
        id="blocked-notice",
        conversation_id=fixture.conversation.id,
        user_id="caller",
        session_id="session",
        native_run={
            "status": "blocked",
            "reason": "predecessor_requires_reconciliation",
            "predecessor_interaction_id": fixture.interaction.id,
        },
        native_steps={},
        observability_metrics=[],
        actions=["NativeHarnessAction"],
        directives=[],
        closed=True,
        has_emitted=lambda: True,
        response=RECOVERY_NOTICE,
        utterance="Remember ORCHID-62",
        get_previous_interaction=AsyncMock(return_value=fixture.interaction),
        started_at=fixture.interaction.started_at,
    )
    prepared = await history_for_new_turn(
        notice,
        conversation_id=fixture.conversation.id,
        user_id="caller",
        session_id="session",
        conversation=fixture.conversation,
    )
    assert (
        prepared.source["frontier"] == "operator_verified_effect" and prepared.deferred
    )
    seen = []

    def respond(messages: Any, info: Any) -> ModelResponse:
        assert "file_interface__write_file" not in {t.name for t in info.function_tools}
        assert any(
            "ORCHID-62" in str(part) for message in messages for part in message.parts
        )
        seen.extend(
            p.content
            for m in messages
            for p in m.parts
            if isinstance(p, ToolReturnPart)
            and p.tool_name == "file_interface__write_file"
        )
        return ModelResponse(parts=[TextPart("Verified")])

    next_turn = Interaction(
        conversation_id=fixture.conversation.id, user_id="caller", session_id="session"
    )
    agent = build_native_agent(
        FunctionModel(respond),
        skill_directory=fixture.skill,
        tools=await fixture.action.get_native_tools(),
        access_check=fixture.allow,
        tool_access_check=fixture.allow,
        allow_mutations=False,
        step_store=InteractionStepStore(next_turn, agent_id="agent"),
        conversation_id="session",
        instructions="Continue",
    )
    result = await run_native_agent(
        agent,
        "Confirm",
        run_id="resumed",
        conversation_id="session",
        message_history=prepared.messages,
        deferred_tool_results=prepared.deferred,
    )
    assert (
        result.output == "Verified"
        and seen
        and is_provider_valid(result.all_messages())
    )
    assert fixture.interaction.native_steps == original and fixture.bodies == [1]
    assert fixture.interaction.native_run["status"] == "running"
    with pytest.raises(ValueError, match="cannot be replaced"):
        await effect_recovery.approve_effect_recovery(
            fixture.interaction,
            fixture.conversation,
            **{**approval, "reason": "changed"},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["content", "metadata", "root", "scope", "owner"])
async def test_stale_or_unbound_effect_is_refused(
    tmp_path: Path, monkeypatch: Any, change: str
) -> None:
    fixture = await interrupted(tmp_path, monkeypatch)
    inspected = await effect_recovery.inspect_effect_recovery(
        fixture.interaction, fixture.conversation
    )
    await effect_recovery.approve_effect_recovery(
        fixture.interaction,
        fixture.conversation,
        digest=inspected.facts["digest"],
        actor="admin",
        evidence="File verified",
        reason="Continue",
    )
    if change == "content":
        fixture.path.write_text("different")
    if change == "metadata":
        import os

        stat = fixture.path.stat()
        os.utime(fixture.path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    if change == "root":
        monkeypatch.setattr(
            _core,
            "_get_file_interface",
            AsyncMock(
                return_value=LocalFileInterface(root_dir=str(tmp_path / "other"))
            ),
        )
    if change == "scope":
        fixture.interaction.native_steps["runs"][0]["metadata"][
            "jv_user_id"
        ] = "foreign"
    if change == "owner":
        monkeypatch.setattr(
            effect_recovery,
            "require_dead_native_owner",
            lambda metadata: (_ for _ in ()).throw(ValueError("Live owner")),
        )
    with pytest.raises(NativeHistoryError):
        await history_for_new_turn(
            fixture.interaction,
            conversation_id=fixture.conversation.id,
            user_id="caller",
            session_id="session",
            conversation=fixture.conversation,
        )
    assert fixture.bodies == [1]


@pytest.mark.asyncio
async def test_failed_approval_flush_retains_only_prepared_decision(
    tmp_path: Path, monkeypatch: Any
) -> None:
    fixture = await interrupted(tmp_path, monkeypatch)
    inspected = await effect_recovery.inspect_effect_recovery(
        fixture.interaction, fixture.conversation
    )
    calls = []
    original = effect_recovery.flush_deferred_entities

    async def flush(*args: Any, **kwargs: Any) -> None:
        calls.append(kwargs)
        if len(calls) == 2:
            raise RuntimeError("Approval flush failed")
        await original(*args, **kwargs)

    monkeypatch.setattr(effect_recovery, "flush_deferred_entities", flush)
    with pytest.raises(RuntimeError, match="flush failed"):
        await effect_recovery.approve_effect_recovery(
            fixture.interaction,
            fixture.conversation,
            digest=inspected.facts["digest"],
            actor="admin",
            evidence="Verified",
            reason="Continue",
        )
    assert len(calls) == 2 and all(c["strict"] for c in calls)
    assert fixture.interaction.native_run["effect_recovery"]["status"] == "prepared"
    with pytest.raises(NativeHistoryError):
        await history_for_new_turn(
            fixture.interaction,
            conversation_id=fixture.conversation.id,
            user_id="caller",
            session_id="session",
            conversation=fixture.conversation,
        )
    assert fixture.bodies == [1]
