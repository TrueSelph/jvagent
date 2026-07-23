"""TaskMonitor endpoint error-shape tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from jvspatial.api.exceptions import ResourceNotFoundError


@pytest.mark.asyncio
async def test_task_tick_missing_monitor_raises():
    from jvagent.action.task_monitor.endpoints import task_tick_endpoint

    request = MagicMock()
    with patch(
        "jvagent.action.task_monitor.endpoints.TaskMonitor.find_one",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(ResourceNotFoundError):
            await task_tick_endpoint(request, agent_id="n.Agent.X")
