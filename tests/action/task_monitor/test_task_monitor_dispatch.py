"""TaskMonitor dispatch preserves user-utterance provenance."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.task_monitor.task_monitor import TaskMonitor
from jvagent.memory.task_proactive import ProactiveTaskSpec


@pytest.mark.asyncio
async def test_dispatch_keeps_proactive_directive_out_of_user_utterance(monkeypatch):
    directive = "Prepare the scheduled founder progress summary"
    task_context = "Focus on milestones completed since the previous check-in."

    class Handle:
        id = "task-1"
        task_type = "PROACTIVE"
        data = ProactiveTaskSpec(directive=directive, context=task_context).to_data()
        status = "active"

        async def complete(self, *, result):
            self.result = result
            self.status = "completed"

    handle = Handle()

    class Store:
        async def claim_proactive(self, task_id, lease_id):
            assert task_id == handle.id
            assert lease_id
            return True

        def get(self, task_id):
            assert task_id == handle.id
            return handle

    class Interaction:
        response = "Summary prepared."

        def add_parameter(self, value, source):
            self.parameter = (value, source)

        def add_directives(self, directives, source):
            self.directives = (directives, source)

        async def save(self):
            return None

    captured = {}

    class Walker:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.data = kwargs["data"]
            self.interaction = Interaction()

        async def spawn(self, agent):
            return None

    monkeypatch.setattr(
        "jvagent.action.interact.interact_walker.InteractWalker", Walker
    )
    agent = SimpleNamespace(id="agent-1", get_response_bus=AsyncMock())
    conversation = SimpleNamespace(
        session_id="session-1", user_id="user-1", channel="web"
    )

    result = await TaskMonitor(agent_id="agent-1").dispatch_one(
        agent, conversation, handle, store=Store()
    )

    assert result is True
    assert captured["utterance"] == ""
    assert captured["data"]["proactive_directive"] == directive
    assert captured["data"]["proactive_context"] == task_context
    assert captured["data"]["proactive_task_id"] == handle.id
    assert handle.status == "completed"
