"""PageIndex webhook graph import uses Shape B jvspatial.create_task."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from jvagent.action.pageindex.endpoints import (
    _schedule_background_webhook_graph_import,
)


@pytest.mark.asyncio
async def test_schedule_runs_import_when_create_task_returns_none():
    """Serverless Shape B: create_task awaits the job inline and returns None."""
    import_ran = []

    async def fake_create_task(coro, name=""):
        await coro
        return None

    with (
        patch(
            "jvagent.action.pageindex.endpoints.create_task",
            fake_create_task,
        ),
        patch(
            "jvagent.action.pageindex.endpoints._import_graph_from_staged_storage_path",
            new_callable=AsyncMock,
            side_effect=lambda *a, **k: import_ran.append(True),
        ),
        patch(
            "jvagent.action.pageindex.endpoints._delete_staged_file",
            new_callable=AsyncMock,
        ) as delete_staged,
    ):
        await _schedule_background_webhook_graph_import(
            "Agent:a",
            "staged/graph.json",
            purge=False,
            process_url="https://jvforge.example/v1/artifacts/job-1",
        )
    assert import_ran == [True]
    delete_staged.assert_awaited_once_with("staged/graph.json")


@pytest.mark.asyncio
async def test_schedule_awaits_returned_task():
    """A scheduled object from create_task is awaited before the helper returns."""
    import_ran = []

    class _Scheduled:
        def __init__(self, coro):
            self._coro = coro

        def __await__(self):
            return self._coro.__await__()

    async def fake_create_task(coro, name=""):
        return _Scheduled(coro)

    with (
        patch(
            "jvagent.action.pageindex.endpoints.create_task",
            fake_create_task,
        ),
        patch(
            "jvagent.action.pageindex.endpoints._import_graph_from_staged_storage_path",
            new_callable=AsyncMock,
            side_effect=lambda *a, **k: import_ran.append(True),
        ),
        patch(
            "jvagent.action.pageindex.endpoints._delete_staged_file",
            new_callable=AsyncMock,
        ) as delete_staged,
    ):
        await _schedule_background_webhook_graph_import(
            "Agent:a",
            "staged/graph.json",
            purge=False,
            process_url="https://jvforge.example/v1/artifacts/job-1",
        )
    assert import_ran == [True]
    delete_staged.assert_awaited_once_with("staged/graph.json")
