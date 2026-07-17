"""Action-enum failure must not orphan-cancel healthy flows."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)

pytestmark = pytest.mark.asyncio


async def test_enabled_actions_sets_failure_flag():
    orch = OrchestratorInteractAction()
    agent = MagicMock()
    agent.get_actions_manager = AsyncMock(side_effect=RuntimeError("db blip"))

    result = await orch._enabled_actions(agent)
    assert result == []
    assert orch._actions_enum_failed is True


async def test_enabled_actions_clears_failure_flag_on_success():
    orch = OrchestratorInteractAction()
    mgr = MagicMock()
    mgr.get_all_actions = AsyncMock(return_value=[])
    agent = MagicMock()
    agent.get_actions_manager = AsyncMock(return_value=mgr)

    await orch._enabled_actions(agent)
    assert orch._actions_enum_failed is False
