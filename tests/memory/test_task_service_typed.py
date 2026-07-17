"""Tests for TaskStore with typed TaskHandle integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.memory.task_store import TaskStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_conversation():
    conv = MagicMock()
    conv.tasks = []
    conv.save = AsyncMock()
    return conv


def _make_store(conversation=None):
    conv = conversation or _make_conversation()
    return TaskStore(conv), conv


# ---------------------------------------------------------------------------
# TaskStore.create
# ---------------------------------------------------------------------------


class TestTaskStoreCreate:
    @pytest.mark.asyncio
    async def test_create_creates_active_task(self):
        store, conv = _make_store()
        handle = await store.create(title="test task", description="test task")
        await handle.start()
        assert handle.id
        assert len(conv.tasks) == 1
        task = conv.tasks[0]
        assert task["status"] == "active"
        assert task["description"] == "test task"

    @pytest.mark.asyncio
    async def test_create_sets_owner_action(self):
        store, conv = _make_store()
        handle = await store.create(
            title="task", description="task", owner_action="SkillAction"
        )
        await handle.start()
        task = conv.tasks[0]
        assert task["owner_action"] == "SkillAction"

    @pytest.mark.asyncio
    async def test_create_saves_conversation(self):
        store, conv = _make_store()
        await store.create(title="t", description="t")
        conv.save.assert_called()

    @pytest.mark.asyncio
    async def test_create_with_data(self):
        store, conv = _make_store()
        await store.create(
            title="t", description="t", data={"iterations": 0, "tools_called": []}
        )
        task = conv.tasks[0]
        assert task["data"]["iterations"] == 0


# ---------------------------------------------------------------------------
# TaskHandle.complete
# ---------------------------------------------------------------------------


class TestTaskHandleComplete:
    @pytest.mark.asyncio
    async def test_complete_transitions_to_terminal(self):
        store, conv = _make_store()
        handle = await store.create(title="t", description="t")
        await handle.start()
        await handle.complete()
        assert conv.tasks[0]["status"] == "completed"
        assert conv.tasks[0]["completed_at"] is not None

    @pytest.mark.asyncio
    async def test_complete_idempotent_when_already_completed(self):
        store, conv = _make_store()
        handle = await store.create(title="t", description="t")
        await handle.start()
        await handle.complete()
        # Completing again should be a no-op, not raise
        await handle.complete()
        assert conv.tasks[0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_complete_sets_result(self):
        store, conv = _make_store()
        handle = await store.create(title="t", description="t")
        await handle.start()
        await handle.complete(result="Done.")
        task = conv.tasks[0]
        assert task["data"].get("result") == "Done."


# ---------------------------------------------------------------------------
# TaskStore.get / list
# ---------------------------------------------------------------------------


class TestTaskStoreGet:
    @pytest.mark.asyncio
    async def test_get_returns_task_handle(self):
        store, conv = _make_store()
        handle = await store.create(title="typed task", description="typed task")
        await handle.start()
        retrieved = store.get(handle.id)
        assert retrieved is not None
        assert retrieved.description == "typed task"
        assert retrieved.status == "active"

    @pytest.mark.asyncio
    async def test_get_returns_none_when_not_found(self):
        store, _ = _make_store()
        assert store.get("nonexistent") is None

    @pytest.mark.asyncio
    async def test_list_returns_handles(self):
        store, conv = _make_store()
        h1 = await store.create(title="t1", description="t1", owner_action="A")
        await h1.start()
        h2 = await store.create(title="t2", description="t2", owner_action="A")
        await h2.start()
        handles = store.list(owner_action="A")
        assert len(handles) == 2
        assert {h.title for h in handles} == {"t1", "t2"}


# ---------------------------------------------------------------------------
# TaskHandle.fail
# ---------------------------------------------------------------------------


class TestTaskHandleFail:
    @pytest.mark.asyncio
    async def test_fail_sets_failure_reason(self):
        store, conv = _make_store()
        handle = await store.create(title="t", description="t")
        await handle.start()
        await handle.fail(reason="tool exploded")
        task = conv.tasks[0]
        assert task["status"] == "failed"
        assert "tool exploded" in task["data"].get("failure_reason", "")


# ---------------------------------------------------------------------------
# TaskStore.sweep_terminal
# ---------------------------------------------------------------------------


class TestTaskStoreSweepTerminal:
    @pytest.mark.asyncio
    async def test_sweep_terminal_removes_old_tasks(self):
        store, conv = _make_store()
        handle = await store.create(title="stale", description="stale")
        await handle.start()
        await handle.complete()
        # Manually backdate completed_at
        conv.tasks[0]["completed_at"] = "2020-01-01T00:00:00+00:00"
        count = await store.sweep_terminal(older_than_seconds=60)
        assert count == 1
        assert len(conv.tasks) == 0

    @pytest.mark.asyncio
    async def test_sweep_terminal_ignores_active_tasks(self):
        store, conv = _make_store()
        handle = await store.create(title="t", description="t")
        await handle.start()
        # Active tasks should not be removed
        count = await store.sweep_terminal(older_than_seconds=60)
        assert count == 0
        assert len(conv.tasks) == 1
        assert conv.tasks[0]["status"] == "active"

    @pytest.mark.asyncio
    async def test_sweep_terminal_no_stale(self):
        store, conv = _make_store()
        handle = await store.create(title="fresh", description="fresh")
        await handle.start()
        await handle.complete()
        count = await store.sweep_terminal(older_than_seconds=3600)
        assert count == 0
        assert len(conv.tasks) == 1


# ---------------------------------------------------------------------------
# add_event
# ---------------------------------------------------------------------------


class TestTaskHandleAddEvent:
    @pytest.mark.asyncio
    async def test_add_event_appends_to_data(self):
        store, conv = _make_store()
        handle = await store.create(title="t", description="t", data={"steps": []})
        await handle.start()
        await handle.add_event(
            event_type="thinking",
            iteration=1,
            details={"tokens": 250},
        )
        task = conv.tasks[0]
        events = task["data"].get("_events", [])
        assert len(events) == 1
        assert events[0]["type"] == "thinking"
        assert events[0]["iteration"] == 1

    @pytest.mark.asyncio
    async def test_add_event_preserves_tool_details(self):
        store, conv = _make_store()
        handle = await store.create(title="t", description="t")
        await handle.start()
        await handle.add_event(
            event_type="tool_call",
            iteration=1,
            details={"tools": ["search", "read"]},
        )
        task = conv.tasks[0]
        events = task["data"].get("_events", [])
        assert events[0]["details"]["tools"] == ["search", "read"]

    @pytest.mark.asyncio
    async def test_add_event_accumulates_multiple(self):
        store, conv = _make_store()
        handle = await store.create(title="t", description="t")
        await handle.start()
        await handle.add_event(
            event_type="thinking", iteration=1, details={"tokens": 100}
        )
        await handle.add_event(
            event_type="thinking", iteration=2, details={"tokens": 200}
        )
        task = conv.tasks[0]
        events = task["data"].get("_events", [])
        assert len(events) == 2
        assert events[0]["details"]["tokens"] == 100
        assert events[1]["details"]["tokens"] == 200

    @pytest.mark.asyncio
    async def test_add_event_caps_at_max(self):
        from jvagent.memory.task_store import MAX_TASK_EVENTS

        store, conv = _make_store()
        handle = await store.create(title="t", description="t")
        await handle.start()
        for i in range(MAX_TASK_EVENTS + 25):
            await handle.add_event(event_type="thinking", iteration=i, details={"i": i})
        events = conv.tasks[0]["data"].get("_events", [])
        assert len(events) == MAX_TASK_EVENTS
        assert events[0]["details"]["i"] == 25
        assert events[-1]["details"]["i"] == MAX_TASK_EVENTS + 24
