"""``BridgeState`` persistence across walker revisits.

Verifies that ``visitor._bridge_state`` is created on first visit, preserved
across :meth:`BridgeInteractAction.execute` calls when the helm re-enqueues
itself, and cleared when the turn finalises.
"""

from __future__ import annotations

import pytest

from jvagent.action.bridge.state import (
    BRIDGE_STATE_VISITOR_ATTR,
    BridgeState,
)
from jvagent.action.helm.contracts import EMIT

pytestmark = pytest.mark.asyncio


async def test_state_initialised_on_first_visit(make_bridge, make_visitor, stub_helm):
    helm = stub_helm(name="A", script=[EMIT(text="hi", finalize=False)])
    bridge = make_bridge(helms={"A": helm}, default_helm="A")
    visitor = make_visitor()

    # ``visitor`` is a MagicMock whose ``__getattr__`` auto-creates child
    # mocks; check the real instance ``__dict__`` to see whether Bridge has
    # attached state via ``setattr``.
    assert BRIDGE_STATE_VISITOR_ATTR not in visitor.__dict__
    await bridge.execute(visitor)

    state = getattr(visitor, BRIDGE_STATE_VISITOR_ATTR)
    assert isinstance(state, BridgeState)
    assert state.current_helm == "A"
    assert state.shift_count == 1  # initial helm resolution recorded
    assert state.turn_started_at > 0


async def test_state_persists_across_revisits(make_bridge, make_visitor, stub_helm):
    """Two scripted visits with non-finalising EMITs leave state intact."""
    helm = stub_helm(
        name="A",
        script=[
            EMIT(text="part1", finalize=False),
            EMIT(text="part2", finalize=False),
        ],
    )
    bridge = make_bridge(helms={"A": helm}, default_helm="A")
    visitor = make_visitor()

    await bridge.execute(visitor)
    state_after_1 = getattr(visitor, BRIDGE_STATE_VISITOR_ATTR)
    last_emit_1 = state_after_1.last_emit_at

    await bridge.execute(visitor)
    state_after_2 = getattr(visitor, BRIDGE_STATE_VISITOR_ATTR)

    # Same state object across visits.
    assert state_after_2 is state_after_1
    # Helm called twice (once per visit) — one model call per walker visit.
    assert helm.call_count == 2
    # last_emit_at advanced.
    assert state_after_2.last_emit_at is not None
    assert state_after_2.last_emit_at >= last_emit_1
    # Only one shift recorded (the initial resolution).
    assert state_after_2.shift_count == 1


async def test_state_cleared_on_final_emit(make_bridge, make_visitor, stub_helm):
    helm = stub_helm(
        name="A",
        script=[
            EMIT(text="part1", finalize=False),
            EMIT(text="part2", finalize=True),
        ],
    )
    bridge = make_bridge(helms={"A": helm}, default_helm="A")
    visitor = make_visitor()

    await bridge.execute(visitor)
    assert hasattr(visitor, BRIDGE_STATE_VISITOR_ATTR)
    await bridge.execute(visitor)
    assert not hasattr(visitor, BRIDGE_STATE_VISITOR_ATTR)


async def test_default_helm_falls_back_to_first_in_helms(
    make_bridge, make_visitor, stub_helm
):
    a = stub_helm(name="A", script=[EMIT(text="from A", finalize=True)])
    b = stub_helm(name="B", script=[EMIT(text="from B", finalize=True)])
    # No ``default_helm`` set — Bridge should pick the first declared helm.
    bridge = make_bridge(
        helms={"A": a, "B": b},
        helm_names=["A", "B"],
    )
    visitor = make_visitor()

    await bridge.execute(visitor)

    # A ran, B did not.
    assert a.call_count == 1
    assert b.call_count == 0
