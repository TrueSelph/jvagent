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
)
from jvagent.memory.task_store import TaskStore

pytestmark = pytest.mark.asyncio


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
