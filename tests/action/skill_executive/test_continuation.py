"""Active-flow awareness (ADR-0012, model-mediated continuation).

An active flow's control-task makes its owner the active flow; the orchestrator
surfaces it as routable context (it does NOT force-resume). Continuing the flow
is ordinary tool selection.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.skill_executive.continuation import (
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
        title="flow", description=owner, task_type="INTERVIEW", owner_action=owner
    )
    await h.start()


async def test_no_active_flow_returns_none():
    assert active_flow_owner(_visitor()) is None


async def test_active_flow_owner_resolved_from_task():
    v = _visitor()
    await _seed_active(v.conversation, "SignupIA")
    assert active_flow_owner(v) == "SignupIA"


async def test_active_flow_note_names_the_tool():
    note = active_flow_note("SignupIA")
    assert "SignupIA" in note
    assert "unrelated" in note or "changed topic" in note  # off-topic guidance
