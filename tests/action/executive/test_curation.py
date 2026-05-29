"""Walker-queue curation (ADR-0010 §2.4, amended 2026-05-29).

The Executive curates the remaining walker queue to {self + always_execute IAs}
so routable IAs (anchored, non-always-execute) run ONLY via the IA center and
do not self-execute as weight-chain members. Regression for the live-smoke
finding where the signup interview ran at weight -40 alongside the Executive.

Also covers Fix A: center purposes reach the routing prompt.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.executive.executive_interact_action import ExecutiveInteractAction
from jvagent.action.interact.base import InteractAction

pytestmark = pytest.mark.asyncio


class _AlwaysIA(InteractAction):
    async def execute(self, visitor):  # pragma: no cover - not invoked here
        return None


class _RoutableIA(InteractAction):
    async def execute(self, visitor):  # pragma: no cover - not invoked here
        return None


def _agent_with_actions(actions):
    mgr = MagicMock()
    mgr.get_all_actions = AsyncMock(return_value=actions)
    agent = MagicMock()
    agent.get_actions_manager = AsyncMock(return_value=mgr)
    return agent


async def test_curation_keeps_self_and_always_execute_drops_routable(monkeypatch):
    ex = ExecutiveInteractAction()

    always = _AlwaysIA()
    always.always_execute = True
    always.weight = 10

    routable = _RoutableIA()
    routable.always_execute = False
    routable.weight = -40

    agent = _agent_with_actions([always, routable, ex])

    visitor = MagicMock()
    visitor.curate_walk_path = AsyncMock()

    await ex._curate_walker_queue(visitor, agent)

    visitor.curate_walk_path.assert_awaited_once()
    curated = visitor.curate_walk_path.await_args.args[0]
    # self first, then always_execute; routable dropped.
    assert curated[0] is ex
    assert always in curated
    assert routable not in curated


async def test_curation_noop_without_agent():
    ex = ExecutiveInteractAction()
    visitor = MagicMock()
    visitor.curate_walk_path = AsyncMock()
    await ex._curate_walker_queue(visitor, None)
    visitor.curate_walk_path.assert_not_called()


async def test_center_purposes_reach_routing_prompt(make_visitor):
    """Fix A: the routing prompt surfaces each center's purpose, not just name."""
    from jvagent.action.executive.context import TurnContext
    from jvagent.action.executive.state import ModelBudget, WorkingMemory

    ex = ExecutiveInteractAction()
    visitor = make_visitor(utterance="hello")
    visitor.conversation = None
    ctx = TurnContext(
        visitor=visitor,
        wm=WorkingMemory(),
        model_budget=ModelBudget(),
        action=ex,
        center_info=[
            {
                "name": "SkillsCenter",
                "purpose": "tool-using reasoning to complete a task",
            },
            {"name": "IACenter", "purpose": "run anchored rails interact-action flows"},
        ],
    )
    system_prompt, _ = await ex._build_routing_prompt(ctx, ["SkillsCenter", "IACenter"])
    assert "SkillsCenter: tool-using reasoning" in system_prompt
    assert "IACenter: run anchored rails" in system_prompt
