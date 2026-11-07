"""Fresh user turns after settled failures never replay undelivered output."""

import asyncio
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage
from pydantic_ai_harness.step_persistence import RunRecord, ToolEffectRecord

from jvagent.harness.accounting import NativeSpendRecorder
from jvagent.harness.native import build_native_agent, run_native_agent
from jvagent.harness.recovery import history_for_new_turn
from jvagent.harness.spend_store import ConversationSpendStore
from jvagent.harness.step_store import InteractionStepStore
from jvagent.memory.conversation import Conversation
from jvagent.memory.interaction import Interaction


async def failed_turn(
    tmp_path: Path,
    *,
    paid: bool = True,
    validation: bool = False,
    delegated: bool = False,
    seeded_workspace: bool = False,
) -> Interaction:
    directory = tmp_path / "example"
    directory.mkdir(exist_ok=True)
    (directory / "SKILL.md").write_text(
        "---\nname: example\ndescription: Test\nallowed-tools: []\n---\nAnswer.\n"
    )
    previous = Interaction(
        conversation_id="conversation",
        user_id="caller",
        utterance="Remember ORCHID-83",
    )
    previous.native_run = {"run_id": "failed", "status": "running"}
    await previous.save()

    async def check(label: str) -> bool:
        assert delegated and label == "delegate_task"
        return True

    def respond(messages: Any, info: Any) -> ModelResponse:
        if not paid:
            raise RuntimeError("provider unavailable")
        if (
            delegated
            and any(t.name == "delegate_task" for t in info.function_tools)
            and not any(
                isinstance(p, ToolReturnPart) and p.tool_name == "delegate_task"
                for m in messages
                for p in m.parts
            )
        ):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "delegate_task",
                        {"agent_name": "worker", "task": "Remember ORCHID-83"},
                    )
                ]
            )
        return ModelResponse(
            parts=[TextPart("UNDELIVERED-ANSWER")],
            usage=RequestUsage(input_tokens=10, output_tokens=5, cost=Decimal("0.001")),
        )

    store = InteractionStepStore(previous)
    conversation = Conversation()
    await conversation.save()
    agent = build_native_agent(
        FunctionModel(respond, model_name="gpt-4.1"),
        skill_directory=Path("jvagent/skills/delegate") if delegated else directory,
        tools=[],
        delegates=(
            [
                {
                    "name": "worker",
                    "description": "Answer",
                    "skill_directory": str(directory),
                }
            ]
            if delegated
            else []
        ),
        conversation_id="session",
        access_check=check,
        instructions="Host policy",
        step_store=store,
        spend_store=ConversationSpendStore(conversation),
        spend_observer=NativeSpendRecorder(previous, lock=store.lock),
    )
    if validation:

        @agent.output_validator
        def reject(output: str) -> str:
            raise RuntimeError("Validation aborted")

    history = []
    if seeded_workspace:
        seed = build_native_agent(
            FunctionModel(
                lambda messages, info: ModelResponse(parts=[TextPart("seed")])
            ),
            skill_directory=directory,
            tools=[],
            access_check=check,
            instructions="Seed the workspace",
        )
        result = await seed.run("Seed", run_id="seed", conversation_id="session")
        history = result.all_messages()
        assert any(isinstance(m, ModelResponse) and m.workspace_ref for m in history)

    with pytest.raises(UsageLimitExceeded if paid and not validation else RuntimeError):
        await run_native_agent(
            agent,
            previous.utterance,
            conversation_id="session",
            run_id="failed",
            max_cost_usd="2" if validation else "0.00001",
            message_history=history,
        )
    previous.native_run = {**previous.native_run, "status": "failed"}
    previous.response = "The previous request could not complete."
    previous.mark_emitted()
    await previous.save()
    return previous


@pytest.mark.asyncio
async def test_accepted_input_notice_keeps_the_authorized_sdk_workspace(
    tmp_path: Path,
) -> None:
    previous = await failed_turn(tmp_path, validation=True, seeded_workspace=True)
    original = deepcopy(previous.native_steps)
    recovered = await history_for_new_turn(
        previous, conversation_id="conversation", user_id="caller", session_id="session"
    )
    responses = [m for m in recovered.messages if isinstance(m, ModelResponse)]
    assert responses[-1].parts[0].content == previous.response
    assert responses[-1].workspace_ref == responses[-2].workspace_ref
    assert responses[-1].workspace_ref is not None
    assert previous.native_steps == original


def test_host_notice_does_not_resurrect_an_explicitly_cleared_workspace() -> None:
    from pydantic_ai.messages import WorkspaceRef

    from jvagent.harness.recovery import _host_notice

    old = ModelResponse(
        parts=[TextPart("old")],
        workspace_ref=WorkspaceRef(provider="docker", id="owned"),
    )
    cleared = ModelResponse(parts=[TextPart("cleared")], workspace_ref=None)
    assert _host_notice([old], "notice").workspace_ref == old.workspace_ref
    assert _host_notice([old, cleared], "notice").workspace_ref is None
    assert cleared.workspace_ref is None and old.workspace_ref is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("paid", "validation", "delegated"),
    [
        (False, False, False),
        (True, False, False),
        (True, True, False),
        (True, True, True),
    ],
)
async def test_new_request_keeps_input_and_observed_failure_not_undelivered_answer(
    tmp_path: Path,
    paid: bool,
    validation: bool,
    delegated: bool,
) -> None:
    previous = await failed_turn(
        tmp_path, paid=paid, validation=validation, delegated=delegated
    )
    if validation:
        assert "UNDELIVERED-ANSWER" in str(previous.native_steps["snapshots"][-1])
    before = dict(previous.native_steps)
    if not paid:
        with pytest.raises(
            RuntimeError, match="provider usage requires reconciliation"
        ):
            await history_for_new_turn(
                previous,
                conversation_id="conversation",
                user_id="caller",
                session_id="session",
            )
        assert previous.usage["unknown_cost_call_count"] == 1
        return
    history, source = await history_for_new_turn(
        previous,
        conversation_id="conversation",
        user_id="caller",
        session_id="session",
    )
    assert source == {"source_run_id": "failed", "frontier": "accepted_input"}
    assert "ORCHID-83" in str(history)
    assert "The previous request could not complete." in str(history)
    assert "UNDELIVERED-ANSWER" not in str(history)
    assert previous.native_steps == before
    tokens = previous.usage["total_tokens"]
    await history_for_new_turn(
        previous, conversation_id="conversation", user_id="caller", session_id="session"
    )
    assert previous.usage["total_tokens"] == tokens

    from pydantic_ai import Agent

    def respond(messages: Any, info: Any) -> ModelResponse:
        assert "Remember ORCHID-83" in str(messages)
        assert "New independent question" in str(messages)
        assert "UNDELIVERED-ANSWER" not in str(messages)
        return ModelResponse(parts=[TextPart("New answer")])

    assert (
        await Agent(FunctionModel(respond)).run(
            "New independent question", message_history=history
        )
    ).output == "New answer"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "block",
    [
        "caller",
        "conversation",
        "session",
        "running",
        "pending_delivery",
        "delivery_unknown",
        "old",
        "child_running",
        "child_scope",
        "unresolved",
        "late_snapshot",
        "unsafe_snapshot",
        "undelivered",
        "orphan",
        "empty",
        "blocked",
    ],
)
async def test_unsafe_predecessor_does_not_admit_a_new_run(
    tmp_path: Path, block: str
) -> None:
    previous = await failed_turn(tmp_path)
    store = InteractionStepStore(previous)
    user, conversation, session = "caller", "conversation", "session"
    if block == "caller":
        user = "other"
    elif block == "conversation":
        conversation = "other"
    elif block == "session":
        session = "other"
    elif block in {"running", "pending_delivery", "delivery_unknown"}:
        previous.native_run = {**previous.native_run, "status": block}
    elif block == "empty":
        previous.native_run = {}
    elif block == "blocked":
        previous.native_run = {"status": "blocked"}
    elif block == "old":
        previous.native_steps["runs"][0]["metadata"] = {}
    elif block in {"child_running", "child_scope"}:
        await store.register_run(
            RunRecord(
                run_id="child",
                parent_run_id="failed",
                conversation_id="other" if block == "child_scope" else "session",
            )
        )
    elif block == "unresolved":
        await store.record_tool_effect(
            ToolEffectRecord(
                run_id="failed",
                tool_call_id="call",
                tool_name="lookup",
                status="started",
            )
        )
    elif block == "late_snapshot":
        from datetime import datetime, timezone

        previous.native_steps["snapshots"][0]["timestamp"] = (
            datetime.now(timezone.utc) + timedelta(seconds=60)
        ).isoformat()
    elif block == "undelivered":
        from pydantic import TypeAdapter

        snapshot = await store.first_snapshot(run_id="failed")
        snapshot.messages.append(
            ModelResponse(parts=[TextPart("UNDELIVERED-ANSWER")], run_id="failed")
        )
        previous.native_steps["snapshots"][0] = TypeAdapter(type(snapshot)).dump_python(
            snapshot, mode="json"
        )
    elif block == "orphan":
        await store.register_run(RunRecord(run_id="orphan", conversation_id="session"))
    elif block == "unsafe_snapshot":
        previous.native_steps["snapshots"][0]["state"] = "interrupted"
    with pytest.raises(RuntimeError):
        await history_for_new_turn(
            previous, conversation_id=conversation, user_id=user, session_id=session
        )


@pytest.mark.asyncio
async def test_store_registration_is_immutable_and_idempotent() -> None:
    interaction = Interaction(utterance="test")
    await interaction.save()
    store = InteractionStepStore(interaction)
    first = RunRecord(run_id="same", conversation_id="session", registration_id="first")
    second = RunRecord(
        run_id="same", conversation_id="session", registration_id="second"
    )
    results = await asyncio.gather(
        store.register_run(first), store.register_run(second), return_exceptions=True
    )
    assert sum(isinstance(r, ValueError) for r in results) == 1
    assert await store.get_run(run_id="same") == first
    await store.register_run(first)
    await store.register_run(
        RunRecord(run_id="same", conversation_id="session", registration_id="first")
    )
    assert await store.get_run(run_id="same") == first
    assert len(await store.list_runs()) == 1
    with pytest.raises(ValueError, match="already registered"):
        await store.register_run(
            RunRecord(run_id="same", conversation_id="other", registration_id="first")
        )
    assert await store.get_run(run_id="same") == first


@pytest.mark.asyncio
async def test_failure_input_survives_fresh_graph_context(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from jvspatial.core.context import (
        GraphContext,
        clear_default_context,
        set_default_context,
    )
    from jvspatial.db.jsondb import JsonDB

    monkeypatch.setenv("JVSPATIAL_ENABLE_DEFERRED_SAVES", "false")
    path = str(tmp_path / "graph")
    set_default_context(GraphContext(database=JsonDB(base_path=path)))
    try:
        previous = await failed_turn(tmp_path, validation=True)
        identity = previous.id
        set_default_context(GraphContext(database=JsonDB(base_path=path)))
        restored = await Interaction.get(identity)
        assert restored is not None
        history, source = await history_for_new_turn(
            restored,
            conversation_id="conversation",
            user_id="caller",
            session_id="session",
        )
        assert source["frontier"] == "accepted_input"
        assert "ORCHID-83" in str(history)
        assert "UNDELIVERED-ANSWER" not in str(history)
        assert restored.native_run["status"] == "failed"
    finally:
        clear_default_context()


@pytest.mark.asyncio
async def test_cancelled_provider_request_retains_input_and_unknown_receipt(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "example"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: example\ndescription: Test\nallowed-tools: []\n---\nAnswer.\n"
    )
    previous = Interaction(
        conversation_id="conversation", user_id="caller", utterance="Remember CANCEL-42"
    )
    previous.native_run = {"run_id": "cancelled", "status": "running"}
    await previous.save()
    entered = asyncio.Event()

    async def respond(messages: Any, info: Any) -> ModelResponse:
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("Cancellation should propagate")

    async def check(label: str) -> bool:
        return False

    agent = build_native_agent(
        FunctionModel(respond),
        skill_directory=directory,
        tools=[],
        access_check=check,
        instructions="Host policy",
        step_store=InteractionStepStore(previous),
    )
    task = asyncio.create_task(
        run_native_agent(
            agent, previous.utterance, conversation_id="session", run_id="cancelled"
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    previous.native_run = {**previous.native_run, "status": "cancelled"}
    await previous.save()
    with pytest.raises(RuntimeError, match="provider usage requires reconciliation"):
        await history_for_new_turn(
            previous,
            conversation_id="conversation",
            user_id="caller",
            session_id="session",
        )
    assert previous.usage["unknown_cost_call_count"] == 1
    assert previous.usage["model_call_count"] == 0
