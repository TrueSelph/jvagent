"""The opt-in pilot and legacy loop are mutually exclusive turn drivers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.action.orchestrator.pilot.contracts import PilotCaller, PilotSnapshot
from jvagent.memory.conversation import Conversation
from jvagent.memory.task_store import TaskStore


def test_capability_pilot_defaults_allow_routine_long_running_research():
    action = OrchestratorInteractAction()

    assert action.pilot_max_model_requests == 32
    assert action.pilot_max_tool_calls == 48
    assert action.pilot_max_total_tokens == 100_000
    assert action.pilot_max_output_tokens == 20_000
    assert action.pilot_max_runtime_seconds == 300


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("legacy", "legacy"),
        ("capability_pilot", "pilot"),
    ],
)
async def test_execute_turn_selects_exactly_one_driver(monkeypatch, mode, expected):
    ex = OrchestratorInteractAction()
    ex.skill_runtime = mode
    calls = []

    async def record(name, *_args, **_kwargs):
        calls.append(name)

    monkeypatch.setattr(OrchestratorInteractAction, "_curate_walk_path", record)
    monkeypatch.setattr(
        OrchestratorInteractAction,
        "_run_loop",
        lambda *_args, **_kwargs: record("legacy"),
    )
    monkeypatch.setattr(
        OrchestratorInteractAction,
        "_run_capability_pilot",
        lambda *_args, **_kwargs: record("pilot"),
    )
    monkeypatch.setattr(OrchestratorInteractAction, "_settle_conversation_cost", record)
    monkeypatch.setattr(OrchestratorInteractAction, "_finalize_proactive_task", record)
    monkeypatch.setattr(OrchestratorInteractAction, "_egress", record)

    await ex._execute_turn(SimpleNamespace(interaction=object()))

    assert calls.count(expected) == 1
    assert calls.count("legacy") + calls.count("pilot") == 1


@pytest.mark.asyncio
async def test_unknown_skill_runtime_fails_closed(monkeypatch):
    ex = OrchestratorInteractAction()
    ex.skill_runtime = "future_driver"
    monkeypatch.setattr(
        OrchestratorInteractAction,
        "_curate_walk_path",
        AsyncMock(),
    )

    with pytest.raises(ValueError, match="skill_runtime"):
        await ex._execute_turn(SimpleNamespace(interaction=object()))


@pytest.mark.asyncio
async def test_execute_turn_settles_usage_even_when_driver_fails(monkeypatch):
    ex = OrchestratorInteractAction()
    ex.skill_runtime = "capability_pilot"
    settled = []

    async def record_settlement(_self, visitor):
        settled.append(visitor)

    async def fail_driver(_self, visitor):
        raise RuntimeError("driver failed after recording provider usage")

    monkeypatch.setattr(OrchestratorInteractAction, "_curate_walk_path", AsyncMock())
    monkeypatch.setattr(
        OrchestratorInteractAction, "_run_capability_pilot", fail_driver
    )
    monkeypatch.setattr(
        OrchestratorInteractAction, "_settle_conversation_cost", record_settlement
    )
    visitor = SimpleNamespace(interaction=object())

    with pytest.raises(RuntimeError, match="driver failed"):
        await ex._execute_turn(visitor)
    assert settled == [visitor]


@pytest.mark.asyncio
async def test_legacy_selection_parks_pilot_work_before_loop(monkeypatch, test_db):
    ex = OrchestratorInteractAction()
    ex.skill_runtime = "legacy"
    conversation = await Conversation.create(
        session_id="legacy-driver-selects-pilot-rollback",
        user_id="pilot-rollback-user",
        channel="default",
    )
    task = await TaskStore(conversation).create(
        title="pilot run",
        description="snapshot survives rollback",
        task_type="CAPABILITY_PILOT",
        owner_action="research",
        initial_status="active",
        snapshot=PilotSnapshot(
            caller=PilotCaller(
                agent_id="a",
                user_id="pilot-rollback-user",
                session_id=conversation.session_id,
            ),
            skill_id="research",
            skill_digest="skill-digest",
            config_digest="config-digest",
        ).model_dump(mode="json"),
    )

    async def record(*_args, **_kwargs):
        return None

    async def legacy_loop(_action, visitor):
        assert TaskStore(visitor.conversation).get(task.id).status == "parked"

    monkeypatch.setattr(OrchestratorInteractAction, "_curate_walk_path", record)
    monkeypatch.setattr(OrchestratorInteractAction, "_run_loop", legacy_loop)
    monkeypatch.setattr(OrchestratorInteractAction, "_run_capability_pilot", record)
    monkeypatch.setattr(OrchestratorInteractAction, "_settle_conversation_cost", record)
    monkeypatch.setattr(OrchestratorInteractAction, "_finalize_proactive_task", record)
    monkeypatch.setattr(OrchestratorInteractAction, "_egress", record)

    await ex._execute_turn(
        SimpleNamespace(interaction=object(), conversation=conversation)
    )

    parked = TaskStore(conversation).get(task.id)
    assert parked is not None
    assert parked.status == "parked"
    assert parked.snapshot["status"] == "parked"
    assert parked.snapshot["evidence"] == []
