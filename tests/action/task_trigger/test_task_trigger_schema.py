"""TaskTriggerInteractAction reads TaskStore ``data`` schema."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.task_trigger_interact_action.task_trigger_interact_action import (
    TaskTriggerInteractAction,
)
from jvagent.memory.task_store import TaskStore

pytestmark = pytest.mark.asyncio


def _visitor(conversation, utterance="I'm busy now"):
    interaction = MagicMock()
    interaction.utterance = utterance
    interaction.inner_monologue = ""
    interaction.save = AsyncMock()

    store = TaskStore(conversation)

    def get_tasks(status=None, owner_action=None):
        handles = store.list(status=status, owner_action=owner_action)
        return [h.to_dict() for h in handles]

    conversation.get_tasks = get_tasks

    visitor = MagicMock()
    visitor.conversation = conversation
    visitor.interaction = interaction
    visitor.tasks = store
    visitor.add_directive = AsyncMock()
    return visitor


async def test_triggers_proactive_task_from_data_schema(monkeypatch):
    conversation = MagicMock()
    conversation.tasks = []
    conversation.save = AsyncMock()

    store = TaskStore(conversation)
    handle = await store.create(
        title="Check back",
        description="Follow up on training",
        task_type="PROACTIVE",
        owner_action="TaskCreationInteractAction",
        data={
            "context": "User asked to check back later",
            "trigger_condition": "busy",
            "trigger_at": "2020-01-01T00:00",
        },
    )
    await handle.start()

    action = TaskTriggerInteractAction()
    v = _visitor(conversation, utterance="I'm busy now")

    async def _now(_app):
        return datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(
        "jvagent.action.task_trigger_interact_action.task_trigger_interact_action.app_now_aware_utc",
        _now,
    )
    monkeypatch.setattr(
        "jvagent.action.task_trigger_interact_action.task_trigger_interact_action.App.get",
        AsyncMock(return_value=MagicMock()),
    )

    await action.execute(v)

    v.add_directive.assert_awaited_once()
    directive = v.add_directive.await_args.args[0]
    assert "Follow up on training" in directive
    assert "check back later" in directive.lower()
    refreshed = store.get(handle.id)
    assert refreshed is not None and refreshed.status == "completed"


async def test_runs_before_skill_executive_weight():
    assert TaskTriggerInteractAction().weight < -200
