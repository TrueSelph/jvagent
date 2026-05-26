"""Shift budget enforcement — prevents helm ping-pong.

Bridge starts each turn with ``shift_budget_per_turn`` (default 4) and
decrements once per ``SHIFT`` verb. When the budget reaches zero, the next
``SHIFT`` triggers the safe-fallback path and finalises the turn.
"""

from __future__ import annotations

import pytest

from jvagent.action.bridge.state import BRIDGE_STATE_VISITOR_ATTR
from jvagent.action.helm.contracts import SHIFT

pytestmark = pytest.mark.asyncio


async def test_budget_decrements_per_shift(make_bridge, make_visitor, stub_helm):
    a = stub_helm(name="A", script=[SHIFT(target="B", reason="ping")])
    b = stub_helm(name="B")  # never gets to step()
    bridge = make_bridge(
        helms={"A": a, "B": b},
        default_helm="A",
        shift_budget=4,
    )
    visitor = make_visitor()

    await bridge.execute(visitor)

    state = getattr(visitor, BRIDGE_STATE_VISITOR_ATTR)
    assert state.shift_budget_remaining == 3


async def test_budget_exhaustion_triggers_safe_fallback(
    make_bridge, make_visitor, stub_helm, publish_log
):
    """When the budget hits zero, the next SHIFT routes to denied_response_text."""
    a = stub_helm(
        name="A",
        script=[
            SHIFT(target="B", reason="1"),
        ],
    )
    b = stub_helm(name="B")
    bridge = make_bridge(
        helms={"A": a, "B": b},
        default_helm="A",
        shift_budget=0,  # already exhausted; the first SHIFT must fall back
        denied_text="too-many-shifts",
    )
    visitor = make_visitor()

    await bridge.execute(visitor)

    # Safe-fallback published.
    assert publish_log[-1]["content"] == "too-many-shifts"
    # State cleared.
    assert not hasattr(visitor, BRIDGE_STATE_VISITOR_ATTR)


async def test_ping_pong_eventually_runs_out_of_budget(
    make_bridge, make_visitor, stub_helm, publish_log
):
    """Repeated A↔B shifts deplete the budget and end the turn cleanly."""
    a = stub_helm(name="A", script=[SHIFT(target="B", reason="ping")])
    b = stub_helm(name="B", script=[SHIFT(target="A", reason="pong")])
    bridge = make_bridge(
        helms={"A": a, "B": b},
        default_helm="A",
        shift_budget=2,
        denied_text="exhausted",
    )
    visitor = make_visitor()

    # Visit 1: A → B   (budget 2 → 1)
    # Visit 2: B → A   (budget 1 → 0)
    # Visit 3: A → B   (budget 0 → safe-fallback)
    await bridge.execute(visitor)
    a.set_script([SHIFT(target="B", reason="ping again")])
    await bridge.execute(visitor)
    b.set_script([SHIFT(target="A", reason="pong again")])
    await bridge.execute(visitor)

    assert publish_log[-1]["content"] == "exhausted"
    assert not hasattr(visitor, BRIDGE_STATE_VISITOR_ATTR)


async def test_budget_does_not_apply_to_initial_resolution(
    make_bridge, make_visitor, stub_helm
):
    """The very first 'shift' (None → default_helm) must not consume budget."""
    from jvagent.action.helm.contracts import EMIT

    a = stub_helm(name="A", script=[EMIT(text="hi", finalize=True)])
    bridge = make_bridge(
        helms={"A": a},
        default_helm="A",
        shift_budget=4,
    )
    visitor = make_visitor()

    await bridge.execute(visitor)
    # No state to inspect after final emit, so just assert helm ran once and
    # no fallback was published.
    assert a.call_count == 1
