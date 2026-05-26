"""AccessControl integration tests.

Resource taxonomy under Bridge:

- ``tool:helm:{name}`` — gates SHIFT targets.
- ``tool:delegate:{action_name}`` — gates DELEGATE targets.

Behaviour matrix:

- No ``AccessControlAction`` attached → allow (fail-open default, mirrors cockpit).
- AC present but not enforcing → allow.
- AC enforcing, label denied → route to safe-fallback.
- AC enforcing, label allowed → proceed.
- AC ``has_action_access`` raises → fail closed (denied).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.bridge import access as bridge_access
from jvagent.action.bridge.state import BRIDGE_STATE_VISITOR_ATTR
from jvagent.action.helm.contracts import DELEGATE, SHIFT

from .conftest import make_ac, make_agent_with_ac

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Resource label helpers (taxonomy lock-in)
# ---------------------------------------------------------------------------


async def test_helm_resource_label_format():
    assert bridge_access.helm_resource_label("ReflexHelm") == "tool:helm:ReflexHelm"


async def test_delegate_resource_label_format():
    assert (
        bridge_access.delegate_resource_label("jvagent/handoff_interact")
        == "tool:delegate:jvagent/handoff_interact"
    )


# ---------------------------------------------------------------------------
# SHIFT gating
# ---------------------------------------------------------------------------


async def test_shift_passthrough_when_no_access_control(
    make_bridge, make_visitor, stub_helm
):
    a = stub_helm(name="A", script=[SHIFT(target="B", reason="hand off")])
    b = stub_helm(name="B")
    agent = MagicMock()
    agent.get_access_control_action = AsyncMock(return_value=None)
    bridge = make_bridge(helms={"A": a, "B": b}, default_helm="A", agent=agent)
    visitor = make_visitor()

    await bridge.execute(visitor)

    state = getattr(visitor, BRIDGE_STATE_VISITOR_ATTR)
    assert state.current_helm == "B"


async def test_shift_passthrough_when_ac_not_enforcing(
    make_bridge, make_visitor, stub_helm
):
    a = stub_helm(name="A", script=[SHIFT(target="B", reason="hand off")])
    b = stub_helm(name="B")
    ac = make_ac(deny_labels={"tool:helm:B"}, enforcing=False)
    agent = make_agent_with_ac(ac)
    bridge = make_bridge(helms={"A": a, "B": b}, default_helm="A", agent=agent)
    visitor = make_visitor()

    await bridge.execute(visitor)

    state = getattr(visitor, BRIDGE_STATE_VISITOR_ATTR)
    assert state.current_helm == "B"


async def test_shift_denied_routes_to_safe_fallback(
    make_bridge, make_visitor, stub_helm, publish_log
):
    a = stub_helm(name="A", script=[SHIFT(target="B", reason="hand off")])
    b = stub_helm(name="B")
    ac = make_ac(deny_labels={"tool:helm:B"})
    agent = make_agent_with_ac(ac)
    bridge = make_bridge(
        helms={"A": a, "B": b},
        default_helm="A",
        agent=agent,
        denied_text="ac-denied",
    )
    visitor = make_visitor()

    await bridge.execute(visitor)

    assert publish_log[-1]["content"] == "ac-denied"
    assert not hasattr(visitor, BRIDGE_STATE_VISITOR_ATTR)


async def test_shift_allowed_when_label_not_denied(
    make_bridge, make_visitor, stub_helm
):
    a = stub_helm(name="A", script=[SHIFT(target="B", reason="hand off")])
    b = stub_helm(name="B")
    ac = make_ac(deny_labels={"tool:helm:OtherHelm"})  # B is allowed
    agent = make_agent_with_ac(ac)
    bridge = make_bridge(helms={"A": a, "B": b}, default_helm="A", agent=agent)
    visitor = make_visitor()

    await bridge.execute(visitor)

    state = getattr(visitor, BRIDGE_STATE_VISITOR_ATTR)
    assert state.current_helm == "B"


async def test_shift_fails_closed_when_ac_raises(
    make_bridge, make_visitor, stub_helm, publish_log
):
    """If ``has_action_access`` raises, Bridge treats the resource as denied."""
    a = stub_helm(name="A", script=[SHIFT(target="B", reason="hand off")])
    b = stub_helm(name="B")
    ac = MagicMock()
    ac.policy_applies = MagicMock(return_value=True)
    ac.has_action_access = AsyncMock(side_effect=RuntimeError("ac broken"))
    agent = make_agent_with_ac(ac)
    bridge = make_bridge(
        helms={"A": a, "B": b},
        default_helm="A",
        agent=agent,
        denied_text="ac-fail-closed",
    )
    visitor = make_visitor()

    await bridge.execute(visitor)

    assert publish_log[-1]["content"] == "ac-fail-closed"


# ---------------------------------------------------------------------------
# DELEGATE gating
# ---------------------------------------------------------------------------


async def test_delegate_denied_routes_to_safe_fallback(
    make_bridge, make_visitor, stub_helm, publish_log
):
    a = stub_helm(name="A", script=[DELEGATE(interact_action="HandoffIA")])
    ac = make_ac(deny_labels={"tool:delegate:HandoffIA"})
    agent = make_agent_with_ac(ac)
    bridge = make_bridge(
        helms={"A": a},
        default_helm="A",
        agent=agent,
        denied_text="delegate-denied",
    )
    visitor = make_visitor()

    # Register a fake IA so the only reason we'd fall back is AC.
    class _FakeIA:
        async def execute(self, walker):
            pytest.fail("delegate target executed despite AC denial")

    bridge._test_action_registry["HandoffIA"] = _FakeIA()

    await bridge.execute(visitor)

    assert publish_log[-1]["content"] == "delegate-denied"


async def test_shift_passthrough_when_get_access_control_action_raises(
    make_bridge, make_visitor, stub_helm
):
    """``agent.get_access_control_action`` raising is treated as 'no AC' (allow)."""
    a = stub_helm(name="A", script=[SHIFT(target="B", reason="hand off")])
    b = stub_helm(name="B")
    agent = MagicMock()
    agent.get_access_control_action = AsyncMock(side_effect=RuntimeError("ac down"))
    bridge = make_bridge(helms={"A": a, "B": b}, default_helm="A", agent=agent)
    visitor = make_visitor()

    await bridge.execute(visitor)

    state = getattr(visitor, BRIDGE_STATE_VISITOR_ATTR)
    assert state.current_helm == "B"


async def test_shift_passthrough_when_policy_applies_raises(
    make_bridge, make_visitor, stub_helm
):
    """``ac.policy_applies()`` raising is treated as 'not enforcing' (allow)."""
    a = stub_helm(name="A", script=[SHIFT(target="B", reason="hand off")])
    b = stub_helm(name="B")
    ac = MagicMock()
    ac.policy_applies = MagicMock(side_effect=RuntimeError("policy borked"))
    ac.has_action_access = AsyncMock(return_value=False)  # never called
    agent = make_agent_with_ac(ac)
    bridge = make_bridge(helms={"A": a, "B": b}, default_helm="A", agent=agent)
    visitor = make_visitor()

    await bridge.execute(visitor)

    state = getattr(visitor, BRIDGE_STATE_VISITOR_ATTR)
    assert state.current_helm == "B"


async def test_delegate_allowed_runs_target(make_bridge, make_visitor, stub_helm):
    a = stub_helm(name="A", script=[DELEGATE(interact_action="HandoffIA")])
    ac = make_ac(deny_labels={"tool:delegate:OtherIA"})
    agent = make_agent_with_ac(ac)
    bridge = make_bridge(helms={"A": a}, default_helm="A", agent=agent)
    visitor = make_visitor()

    executed = {"count": 0}

    class _FakeIA:
        async def execute(self, walker):
            executed["count"] += 1

    bridge._test_action_registry["HandoffIA"] = _FakeIA()

    await bridge.execute(visitor)

    assert executed["count"] == 1
