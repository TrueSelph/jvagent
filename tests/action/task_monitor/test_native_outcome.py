"""A native notice is not a successful proactive task or permission to retry."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.task_monitor.finalize import finalize_proactive_task
from jvagent.memory.task_proactive import ProactiveTaskSpec


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", ["blocked", "failed", "cancelled", "delivery_unknown"]
)
async def test_native_notice_cannot_complete_or_requeue_task(status: str) -> None:
    handle = SimpleNamespace(
        status="active",
        task_type="PROACTIVE",
        data=ProactiveTaskSpec(directive="Prepare report").to_data(),
        complete=AsyncMock(),
        fail=AsyncMock(),
    )
    store = SimpleNamespace(get=lambda task_id: handle, requeue_proactive=AsyncMock())
    outcome = await finalize_proactive_task(
        store,
        "task",
        interaction=SimpleNamespace(
            response="The model request could not complete.",
            native_run={"status": status},
        ),
    )
    assert outcome == "failed"
    handle.complete.assert_not_awaited()
    store.requeue_proactive.assert_not_awaited()
    handle.fail.assert_awaited_once()
