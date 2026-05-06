"""Tests for cockpit access-control integration (Milestone F).

Verifies that per-user access policies on the agent's ``AccessControlAction``
filter cockpit's routed skills, routed interact_actions, and tool registry
before the engine runs. Resource taxonomy:

- skills: ``skill:{name}``
- interact_actions: class name (existing convention)
- tools: ``tool:{tool_name}``
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.cockpit import access
from jvagent.action.cockpit.routing_types import POSTURE_RESPOND, RoutingResult
from jvagent.action.interact.base import InteractAction
from jvagent.tooling.tool import Tool
from jvagent.tooling.tool_registry import ToolRegistry

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


def _make_ac(*, deny_labels=None, default_allow=True, enforcing=True):
    """Build a fake AccessControlAction.

    Denies any label in ``deny_labels``; allows everything else when
    ``default_allow`` is True. Treated as 'not enforcing' (cockpit no-ops)
    when ``enforcing=False``.
    """
    deny = set(deny_labels or [])

    async def _check(*, user_id, action_label, channel):
        if action_label in deny:
            return False
        return default_allow

    ac = MagicMock()
    ac.policy_applies = MagicMock(return_value=enforcing)
    ac.has_action_access = AsyncMock(side_effect=_check)
    return ac


def _make_agent(ac):
    agent = MagicMock()
    agent.id = "agent_test"
    agent.get_access_control_action = AsyncMock(return_value=ac)
    return agent


def _make_ia(cls_name: str, weight: int = 0):
    fake = MagicMock(spec=InteractAction)
    fake.__class__ = type(cls_name, (InteractAction,), {})
    fake.weight = weight
    fake.id = f"ia_{cls_name}"
    return fake


# ---------------------------------------------------------------------------
# Skill filter
# ---------------------------------------------------------------------------


async def test_filter_routed_skills_strips_denied():
    ac = _make_ac(deny_labels={"skill:web_search"})
    agent = _make_agent(ac)
    routing = RoutingResult(
        posture=POSTURE_RESPOND, actions=["web_search", "pageindex_search"]
    )

    out = await access.filter_routed_skills_by_access(
        agent, routing, user_id="u1", channel="default"
    )
    assert out == ["pageindex_search"]


async def test_filter_routed_skills_passthrough_when_no_access_control():
    agent = _make_agent(None)
    routing = RoutingResult(posture=POSTURE_RESPOND, actions=["a", "b"])

    out = await access.filter_routed_skills_by_access(
        agent, routing, user_id="u1", channel="default"
    )
    assert out == ["a", "b"]


async def test_filter_routed_skills_passthrough_when_not_enforcing():
    ac = _make_ac(deny_labels={"skill:a"}, enforcing=False)
    agent = _make_agent(ac)
    routing = RoutingResult(posture=POSTURE_RESPOND, actions=["a", "b"])

    out = await access.filter_routed_skills_by_access(
        agent, routing, user_id="u1", channel="default"
    )
    assert out == ["a", "b"]


async def test_filter_routed_skills_empty_input_returns_empty():
    ac = _make_ac()
    agent = _make_agent(ac)
    routing = RoutingResult(posture=POSTURE_RESPOND, actions=[])
    out = await access.filter_routed_skills_by_access(
        agent, routing, user_id="u1", channel="default"
    )
    assert out == []


# ---------------------------------------------------------------------------
# InteractAction filter
# ---------------------------------------------------------------------------


async def test_filter_routed_ias_strips_denied_class():
    ac = _make_ac(deny_labels={"HandoffInteractAction"})
    agent = _make_agent(ac)
    handoff = _make_ia("HandoffInteractAction")
    intro = _make_ia("IntroInteractAction")

    out = await access.filter_routed_interact_actions_by_access(
        agent, [handoff, intro], user_id="u1", channel="default"
    )
    assert [a.__class__.__name__ for a in out] == ["IntroInteractAction"]


async def test_filter_routed_ias_passthrough_when_ac_absent():
    agent = _make_agent(None)
    a = _make_ia("OneAction")
    b = _make_ia("TwoAction")

    out = await access.filter_routed_interact_actions_by_access(
        agent, [a, b], user_id="u1", channel="default"
    )
    assert len(out) == 2


# ---------------------------------------------------------------------------
# Tool registry filter
# ---------------------------------------------------------------------------


async def test_filter_tool_registry_removes_denied_tools():
    ac = _make_ac(
        deny_labels={"tool:web_search__search", "tool:memory_set"},
    )
    agent = _make_agent(ac)
    reg = ToolRegistry()
    for n in ("web_search__search", "memory_set", "memory_get", "response_publish"):
        reg.register(
            Tool(
                name=n,
                description=n,
                parameters_schema={"type": "object", "properties": {}},
            )
        )

    removed = await access.filter_tool_registry_by_access(
        reg, agent, user_id="u1", channel="default"
    )
    assert removed == 2
    names = set(reg.names())
    assert "web_search__search" not in names
    assert "memory_set" not in names
    assert "memory_get" in names
    assert "response_publish" in names


async def test_filter_tool_registry_noop_without_ac():
    agent = _make_agent(None)
    reg = ToolRegistry()
    reg.register(
        Tool(
            name="anything",
            description="x",
            parameters_schema={"type": "object", "properties": {}},
        )
    )
    removed = await access.filter_tool_registry_by_access(
        reg, agent, user_id="u1", channel="default"
    )
    assert removed == 0
    assert reg.names() == ["anything"]


async def test_check_failure_fails_closed():
    """If has_action_access raises, _is_allowed returns False (deny)."""
    ac = MagicMock()
    ac.policy_applies = MagicMock(return_value=True)
    ac.has_action_access = AsyncMock(side_effect=RuntimeError("ac broken"))

    out = await access._is_allowed(
        ac,
        user_id="u1",
        channel="default",
        label="tool:foo",
    )
    assert out is False


# ---------------------------------------------------------------------------
# Resource label helpers (taxonomy contract)
# ---------------------------------------------------------------------------


async def test_resource_label_helpers():
    assert access.skill_resource_label("web_search") == "skill:web_search"
    assert access.tool_resource_label("memory_set") == "tool:memory_set"
    assert (
        access.interact_action_resource_label("HandoffInteractAction")
        == "HandoffInteractAction"
    )
