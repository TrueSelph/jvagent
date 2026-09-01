"""Tests for separate interact vs general action cache keys (C2)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jvagent.core.cache import (
    cache_actions,
    cache_interact_actions,
    get_cached_actions,
    get_cached_interact_actions,
    invalidate_action_cache,
)


@pytest.mark.asyncio
async def test_interact_cache_key_isolated_from_general_action_cache():
    agent_id = "n.Agent.test"
    await invalidate_action_cache(agent_id)

    general = [SimpleNamespace(label="ReplyAction", enabled=True)]
    interact = [SimpleNamespace(label="Orchestrator", weight=-200, enabled=True)]

    await cache_actions(agent_id, general, enabled_only=True)
    await cache_interact_actions(agent_id, interact, enabled_only=True)

    assert await get_cached_actions(agent_id) is general
    cached_interact = await get_cached_interact_actions(agent_id)
    assert cached_interact is interact
    assert cached_interact is not general
