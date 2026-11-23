"""Active-flow awareness (ADR-0012, model-mediated continuation).

An active flow's control-task makes its owner the active flow; the orchestrator
surfaces it as routable context (it does NOT force-resume). Continuing the flow
is ordinary tool selection.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.orchestrator.continuation import (
    active_flow_note,
    active_flow_owner,
    note_locked_flow_error,
    park_capability_pilot_tasks,
)
from jvagent.memory.conversation import Conversation
from jvagent.memory.task_store import TaskStore


def _visitor():
    conversation = MagicMock()
    conversation.context = {}
    conversation.tasks = []
    conversation.save = AsyncMock()
    visitor = MagicMock()
    visitor.conversation = conversation
    return visitor


async def _seed_active(conversation, owner):
    h = await TaskStore(conversation).create(
        title="flow", description=owner, task_type="SKILL", owner_action=owner
    )
    await h.start()


async def test_no_active_flow_returns_none():
    assert active_flow_owner(_visitor()) is None


async def test_active_flow_owner_resolved_from_task():
    v = _visitor()
    await _seed_active(v.conversation, "SignupIA")
    assert active_flow_owner(v, flow_tool_names={"SignupIA"}) == "SignupIA"


@pytest.mark.asyncio
async def test_capability_pilot_task_is_not_a_legacy_active_flow():
    from jvagent.memory.task_store import TaskStore

    conversation = _visitor().conversation
    await TaskStore(conversation).create(
        title="research",
        description="pilot research",
        owner_action="research",
        task_type="CAPABILITY_PILOT",
    )
    task = TaskStore(conversation).list()[0]
    await task.start()

    visitor = MagicMock()
    visitor.conversation = conversation
    assert active_flow_owner(visitor) is None


@pytest.mark.asyncio
async def test_legacy_failure_abandonment_does_not_cancel_pilot_task_same_owner(
    test_db,
):
    conversation = await Conversation.create(
        session_id="legacy-failure-pilot-isolation",
        user_id="pilot-isolation-user",
        channel="default",
    )
    store = TaskStore(conversation)
    legacy = await store.create(
        title="legacy flow",
        description="legacy research task",
        task_type="SKILL",
        owner_action="research",
    )
    pilot = await store.create(
        title="pilot research",
        description="pilot research task",
        task_type="CAPABILITY_PILOT",
        owner_action="research",
    )
    await legacy.start()
    await pilot.start()

    visitor = MagicMock()
    visitor.conversation = conversation
    abandoned = await note_locked_flow_error(visitor, "research", limit=1)

    assert abandoned is True
    status_by_type = {task.task_type: task.status for task in store.list()}
    assert status_by_type["SKILL"] == "cancelled"
    assert status_by_type["CAPABILITY_PILOT"] == "active"


@pytest.mark.asyncio
async def test_legacy_driver_parks_pilot_snapshot_and_flags_uncertain_invocation(
    test_db,
):
    conversation = await Conversation.create(
        session_id="legacy-driver-pilot-rollback",
        user_id="pilot-rollback-user",
        channel="default",
    )
    store = TaskStore(conversation)
    snapshot = {
        "schema_version": 3,
        "driver": "capability_pilot",
        "status": "running",
        "evidence": [{"source_id": "source-1"}],
        "invocations": [{"invocation_id": "call-1", "status": "started"}],
        "requires_reconciliation": False,
    }
    task = await store.create(
        title="pilot research",
        description="preserve this snapshot on rollback",
        task_type="CAPABILITY_PILOT",
        owner_action="research",
        initial_status="active",
        snapshot=snapshot,
    )
    visitor = MagicMock()
    visitor.conversation = conversation

    assert await park_capability_pilot_tasks(visitor) == 1

    persisted = TaskStore(conversation).get(task.id)
    assert persisted is not None
    assert persisted.status == "parked"
    assert persisted.snapshot["status"] == "parked"
    assert persisted.snapshot["evidence"] == snapshot["evidence"]
    assert persisted.snapshot["invocations"] == snapshot["invocations"]
    assert persisted.snapshot["requires_reconciliation"] is True


async def test_proactive_task_not_treated_as_flow():
    v = _visitor()
    h = await TaskStore(v.conversation).create(
        title="outreach",
        description="proactive",
        task_type="PROACTIVE",
        owner_action="SomeAction",
    )
    await h.start()
    assert active_flow_owner(v) is None


async def test_flow_owner_requires_routable_tool_name_when_filtered():
    v = _visitor()
    await _seed_active(v.conversation, "SignupIA")
    assert active_flow_owner(v, flow_tool_names={"OtherIA"}) is None
    assert active_flow_owner(v, flow_tool_names={"SignupIA"}) == "SignupIA"


async def test_active_flow_note_names_the_tool():
    note = active_flow_note("SignupIA")
    assert "SignupIA" in note
    assert "unrelated" in note or "changed topic" in note  # off-topic guidance


async def test_agentic_loop_task_not_treated_as_flow():
    v = _visitor()
    h = await TaskStore(v.conversation).create(
        title="loop",
        description="agentic",
        task_type="AGENTIC_LOOP",
        owner_action="OrchestratorInteractAction",
    )
    await h.start()
    assert active_flow_owner(v) is None


async def test_multiple_active_flows_prefers_most_recent():
    v = _visitor()
    older = await TaskStore(v.conversation).create(
        title="old",
        description="OldFlow",
        task_type="SKILL",
        owner_action="OldFlowIA",
    )
    await older.start()
    newer = await TaskStore(v.conversation).create(
        title="new",
        description="NewFlow",
        task_type="SKILL",
        owner_action="NewFlowIA",
    )
    await newer.start()
    # Force updated_at ordering when timestamps collide in fast tests.
    newer._task.updated_at = "2099-01-01T00:00:00+00:00"
    older._task.updated_at = "2000-01-01T00:00:00+00:00"
    await TaskStore(v.conversation)._persist()
    assert (
        active_flow_owner(v, flow_tool_names={"OldFlowIA", "NewFlowIA"}) == "NewFlowIA"
    )
