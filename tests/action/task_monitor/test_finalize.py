"""Tests for task_monitor.finalize module."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.task_monitor.finalize import (
    cancel_expired_pending,
    finalize_proactive_task,
    sweep_terminal_proactive,
)


class TestCancelExpiredPending:
    """Tests for cancel_expired_pending function."""

    @pytest.mark.asyncio
    async def test_no_pending_tasks(self):
        """No pending tasks → 0 cancelled."""
        store = MagicMock()
        store.list_queue = MagicMock(return_value=[])
        now = datetime.now(timezone.utc)
        result = await cancel_expired_pending(store, now=now)
        assert result == 0

    @pytest.mark.asyncio
    async def test_cancel_expired_task(self):
        """Task past not_after → cancelled."""
        now = datetime.now(timezone.utc)
        past = now - timedelta(hours=1)

        handle = MagicMock()
        handle.cancel = AsyncMock()
        handle.task_type = "PROACTIVE"
        handle.data = {
            "spec_version": 2,
            "directive": "test",
            "not_after": past.isoformat(),
        }

        store = MagicMock()
        store.list_queue = MagicMock(return_value=[handle])

        result = await cancel_expired_pending(store, now=now)
        assert result == 1
        handle.cancel.assert_called_once_with(reason="expired")

    @pytest.mark.asyncio
    async def test_keep_future_task(self):
        """Task before not_after → not cancelled."""
        now = datetime.now(timezone.utc)
        future = now + timedelta(hours=1)

        handle = MagicMock()
        handle.cancel = AsyncMock()
        handle.parameters = {"not_after": future.isoformat()}
        handle.task_type = "PROACTIVE"

        store = MagicMock()
        store.list_queue = MagicMock(return_value=[handle])

        result = await cancel_expired_pending(store, now=now)
        assert result == 0
        handle.cancel.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_non_proactive_task(self):
        """Non-proactive task spec → skipped."""
        now = datetime.now(timezone.utc)
        past = now - timedelta(hours=1)

        handle = MagicMock()
        handle.cancel = AsyncMock()
        handle.parameters = {}
        handle.task_type = "other"

        store = MagicMock()
        store.list_queue = MagicMock(return_value=[handle])

        result = await cancel_expired_pending(store, now=now)
        assert result == 0
        handle.cancel.assert_not_called()


class TestSweepTerminalProactive:
    """Tests for sweep_terminal_proactive function."""

    @pytest.mark.asyncio
    async def test_disabled_when_ttl_zero(self):
        """ttl_days=0 → no sweep."""
        store = MagicMock()
        result = await sweep_terminal_proactive(store, ttl_days=0)
        assert result == 0

    @pytest.mark.asyncio
    async def test_remove_old_terminal_task(self):
        """Terminal task older than TTL → removed."""
        now = datetime.now(timezone.utc)
        old_time = now - timedelta(days=40)

        task = SimpleNamespace(
            terminal_at=old_time.isoformat(),
            to_dict=lambda: {"id": "task1"},
        )
        handle = SimpleNamespace(
            task_type="PROACTIVE",
            status="completed",
            _task=task,
        )

        conversation = MagicMock()
        conversation.tasks = [task.to_dict()]

        store = MagicMock()
        store.list = MagicMock(return_value=[handle])
        store._conversation = conversation
        store._persist = AsyncMock()

        result = await sweep_terminal_proactive(store, ttl_days=30, now=now)
        assert result == 1
        assert conversation.tasks == []
        store._persist.assert_called_once()

    @pytest.mark.asyncio
    async def test_keep_recent_terminal_task(self):
        """Terminal task within TTL → kept."""
        now = datetime.now(timezone.utc)
        recent_time = now - timedelta(days=10)

        task = SimpleNamespace(
            terminal_at=recent_time.isoformat(),
            to_dict=lambda: {"id": "task1"},
        )
        handle = SimpleNamespace(
            task_type="PROACTIVE",
            status="completed",
            _task=task,
        )

        conversation = MagicMock()
        conversation.tasks = [task.to_dict()]

        store = MagicMock()
        store.list = MagicMock(return_value=[handle])
        store._conversation = conversation
        store._persist = AsyncMock()

        result = await sweep_terminal_proactive(store, ttl_days=30, now=now)
        assert result == 0
        assert len(conversation.tasks) == 1

    @pytest.mark.asyncio
    async def test_keep_non_terminal_task(self):
        """Non-terminal task → kept."""
        now = datetime.now(timezone.utc)
        old_time = now - timedelta(days=40)

        task = SimpleNamespace(
            terminal_at=old_time.isoformat(),
            to_dict=lambda: {"id": "task1"},
        )
        handle = SimpleNamespace(
            task_type="PROACTIVE",
            status="active",
            _task=task,
        )

        conversation = MagicMock()
        conversation.tasks = [task.to_dict()]

        store = MagicMock()
        store.list = MagicMock(return_value=[handle])
        store._conversation = conversation

        result = await sweep_terminal_proactive(store, ttl_days=30, now=now)
        assert result == 0


class TestFinalizeProactiveTask:
    """Tests for finalize_proactive_task function."""

    @pytest.mark.asyncio
    async def test_task_not_found(self):
        """Task not found → skipped."""
        store = MagicMock()
        store.get = MagicMock(return_value=None)
        result = await finalize_proactive_task(store, "task1")
        assert result == "skipped"

    @pytest.mark.asyncio
    async def test_task_not_active(self):
        """Task not in active status → skipped."""
        handle = MagicMock()
        handle.status = "completed"

        store = MagicMock()
        store.get = MagicMock(return_value=handle)

        result = await finalize_proactive_task(store, "task1")
        assert result == "skipped"

    @pytest.mark.asyncio
    async def test_invalid_proactive_spec(self):
        """Invalid spec → failed."""
        handle = MagicMock()
        handle.status = "active"
        handle.parameters = {}
        handle.task_type = "other"
        handle.fail = AsyncMock()

        store = MagicMock()
        store.get = MagicMock(return_value=handle)

        result = await finalize_proactive_task(store, "task1")
        assert result == "failed"
        handle.fail.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_with_retries(self):
        """Error + attempts remain → requeued."""
        handle = MagicMock()
        handle.status = "active"
        handle.task_type = "PROACTIVE"
        handle.data = {
            "spec_version": 2,
            "directive": "test directive",
            "attempt_count": 0,
            "max_attempts": 3,
        }

        store = MagicMock()
        store.get = MagicMock(return_value=handle)
        store.requeue_proactive = AsyncMock()

        result = await finalize_proactive_task(
            store, "task1", error=ValueError("test error")
        )
        assert result == "requeued"
        store.requeue_proactive.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_no_retries(self):
        """Error + no attempts remain → failed."""
        handle = MagicMock()
        handle.status = "active"
        handle.task_type = "PROACTIVE"
        handle.data = {
            "spec_version": 2,
            "directive": "test directive",
            "attempt_count": 2,
            "max_attempts": 3,
        }
        handle.fail = AsyncMock()

        store = MagicMock()
        store.get = MagicMock(return_value=handle)

        result = await finalize_proactive_task(
            store, "task1", error=ValueError("test error")
        )
        assert result == "failed"
        handle.fail.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_response_with_retries(self):
        """Empty response + attempts remain → requeued."""
        handle = MagicMock()
        handle.status = "active"
        handle.task_type = "PROACTIVE"
        handle.data = {
            "spec_version": 2,
            "directive": "test directive",
            "attempt_count": 0,
            "max_attempts": 3,
        }

        interaction = SimpleNamespace(response="")

        store = MagicMock()
        store.get = MagicMock(return_value=handle)
        store.requeue_proactive = AsyncMock()

        result = await finalize_proactive_task(store, "task1", interaction=interaction)
        assert result == "requeued"

    @pytest.mark.asyncio
    async def test_empty_response_no_retries(self):
        """Empty response + no attempts remain → failed."""
        handle = MagicMock()
        handle.status = "active"
        handle.task_type = "PROACTIVE"
        handle.data = {
            "spec_version": 2,
            "directive": "test directive",
            "attempt_count": 2,
            "max_attempts": 3,
        }
        handle.fail = AsyncMock()

        interaction = SimpleNamespace(response="")

        store = MagicMock()
        store.get = MagicMock(return_value=handle)

        result = await finalize_proactive_task(store, "task1", interaction=interaction)
        assert result == "failed"

    @pytest.mark.asyncio
    async def test_successful_completion(self):
        """Valid response → completed."""
        handle = MagicMock()
        handle.status = "active"
        handle.task_type = "PROACTIVE"
        handle.data = {
            "spec_version": 2,
            "directive": "test directive",
            "attempt_count": 0,
            "max_attempts": 3,
        }
        handle.complete = AsyncMock()

        interaction = SimpleNamespace(response="This is a valid response.")

        store = MagicMock()
        store.get = MagicMock(return_value=handle)

        result = await finalize_proactive_task(store, "task1", interaction=interaction)
        assert result == "completed"
        handle.complete.assert_called_once()
