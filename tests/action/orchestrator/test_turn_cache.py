"""The enabled-action surface is read once per turn, not once per call site.

``Actions.get_all_actions()`` is a per-node database walk (~80 ms on the example
agent) and four independent orchestrator call sites want the same answer inside
one turn.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.action.orchestrator.turn_cache import bind_turn_cache, get_turn_cache


def _agent_with_counter():
    calls = {"n": 0}
    action = MagicMock()

    async def _get_all_actions(enabled_only=False, entity=None):
        calls["n"] += 1
        return [action]

    mgr = MagicMock()
    mgr.get_all_actions = _get_all_actions
    agent = MagicMock()
    agent.id = "n.Agent.abc"
    agent.get_actions_manager = AsyncMock(return_value=mgr)
    return agent, calls


async def test_repeated_reads_inside_one_turn_hit_the_graph_once():
    ex = OrchestratorInteractAction()
    agent, calls = _agent_with_counter()
    with bind_turn_cache():
        for _ in range(4):
            assert len(await ex._enabled_actions(agent)) == 1
    assert calls["n"] == 1


async def test_each_turn_re_reads():
    ex = OrchestratorInteractAction()
    agent, calls = _agent_with_counter()
    for _ in range(3):
        with bind_turn_cache():
            await ex._enabled_actions(agent)
    assert calls["n"] == 3


async def test_no_turn_scope_means_no_caching():
    """Direct calls outside a turn (background work, unit tests) go straight
    through — the memo never changes behaviour, only repetition."""
    ex = OrchestratorInteractAction()
    agent, calls = _agent_with_counter()
    await ex._enabled_actions(agent)
    await ex._enabled_actions(agent)
    assert calls["n"] == 2


async def test_scope_is_per_task_not_per_instance():
    """The Action node is a shared singleton, so the scope holder must be a
    ContextVar: one request's surface must never leak into a concurrent one."""
    ex = OrchestratorInteractAction()
    agent_a, calls_a = _agent_with_counter()
    agent_b, calls_b = _agent_with_counter()
    started = asyncio.Event()

    async def turn(agent, hold: bool):
        with bind_turn_cache():
            await ex._enabled_actions(agent)
            if hold:
                started.set()
                await asyncio.sleep(0.02)
            else:
                await started.wait()
            assert get_turn_cache() is not None
            await ex._enabled_actions(agent)

    await asyncio.gather(turn(agent_a, True), turn(agent_b, False))
    assert calls_a["n"] == 1
    assert calls_b["n"] == 1


async def test_cache_is_cleared_when_the_turn_ends():
    with bind_turn_cache():
        assert get_turn_cache() is not None
    assert get_turn_cache() is None


async def test_enumeration_failure_is_not_cached():
    ex = OrchestratorInteractAction()
    agent = MagicMock()
    agent.id = "n.Agent.boom"
    agent.get_actions_manager = AsyncMock(side_effect=RuntimeError("db blip"))
    with bind_turn_cache():
        assert await ex._enabled_actions(agent) == []
        assert ex._actions_enum_failed is True
        # A later read in the same turn must be able to recover, not be pinned
        # to the empty result — an empty surface cancels healthy flows.
        assert await ex._enabled_actions(agent) == []
    assert ex._actions_enum_failed is True
