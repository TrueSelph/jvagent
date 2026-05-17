"""sync_counters must not save() unless there's drift (AUDIT-core C-5).

The previous implementation always issued an unconditional ``save()`` even
when in-memory counters already matched reality. That made a routine read
endpoint (``GET /api/status?sync=true``) mutate state under load, racing
with concurrent agent install/delete handlers that bumped the counters.
"""

from unittest.mock import AsyncMock, patch

import pytest

from jvagent.core.agents import Agents


@pytest.mark.asyncio
async def test_sync_counters_no_drift_does_not_save():
    """When counters already match reality, save() must NOT be called."""
    agents = Agents()
    agents.total_agents = 3
    agents.active_agents = 2

    fake_connected = [
        type("FakeAgent", (), {"enabled": True})(),
        type("FakeAgent", (), {"enabled": True})(),
        type("FakeAgent", (), {"enabled": False})(),
    ]

    save_mock = AsyncMock()
    with patch.object(
        Agents,
        "get_connected_agents",
        new=AsyncMock(return_value=fake_connected),
    ), patch.object(Agents, "save", new=save_mock):
        result = await agents.sync_counters()

    assert result == {
        "total_agents": 3,
        "active_agents": 2,
        "drift_total": 0,
        "drift_active": 0,
    }
    save_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_counters_with_drift_does_save():
    agents = Agents()
    agents.total_agents = 1  # stale
    agents.active_agents = 0  # stale

    fake_connected = [
        type("FakeAgent", (), {"enabled": True})(),
        type("FakeAgent", (), {"enabled": False})(),
    ]

    save_mock = AsyncMock()
    with patch.object(
        Agents,
        "get_connected_agents",
        new=AsyncMock(return_value=fake_connected),
    ), patch.object(Agents, "save", new=save_mock):
        result = await agents.sync_counters()

    assert result["total_agents"] == 2
    assert result["active_agents"] == 1
    assert result["drift_total"] == 1
    assert result["drift_active"] == 1
    save_mock.assert_awaited_once()
