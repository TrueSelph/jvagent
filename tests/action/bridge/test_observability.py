"""Observability instrumentation tests (BRIDGE-ROADMAP §I).

Covers:

- Per-helm wall-clock and step-count accrual on ``BridgeState``.
- ``helm_shift`` event appended to ``interaction.observability_metrics``
  on the initial helm resolution AND on every ``SHIFT`` verb.
- ``ShiftRecord.to_dict`` round-trip.
- ``bridge_observability`` payload persisted onto
  ``interaction.parameters`` at terminal (EMIT(finalize=True) / YIELD).
- Best-effort: observability never breaks a turn (persistence
  exception is swallowed).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from jvagent.action.bridge.state import BRIDGE_STATE_VISITOR_ATTR
from jvagent.action.helm.contracts import (
    EMIT,
    SHIFT,
    ShiftRecord,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# ShiftRecord.to_dict
# ---------------------------------------------------------------------------


async def test_shift_record_to_dict_serialises_all_fields():
    rec = ShiftRecord(
        from_helm="A",
        to_helm="B",
        reason="hand off",
        ack_emitted=True,
        shift_index=3,
        at_monotonic=12.5,
        handoff_state={"topic": "weather"},
        routing_source="helm_shift",
    )
    d = rec.to_dict()
    assert d == {
        "from_helm": "A",
        "to_helm": "B",
        "reason": "hand off",
        "ack_emitted": True,
        "shift_index": 3,
        "at_monotonic": 12.5,
        "handoff_state": {"topic": "weather"},
        "routing_source": "helm_shift",
    }


async def test_shift_record_routing_source_defaults_to_none():
    """``routing_source`` is optional — None when unset (backwards-compat)."""
    rec = ShiftRecord(
        from_helm=None,
        to_helm="A",
        reason="legacy",
        ack_emitted=False,
        shift_index=0,
        at_monotonic=0.0,
    )
    assert rec.routing_source is None
    assert rec.to_dict()["routing_source"] is None


async def test_shift_record_to_dict_none_handoff():
    rec = ShiftRecord(
        from_helm=None,
        to_helm="A",
        reason="initial",
        ack_emitted=False,
        shift_index=0,
        at_monotonic=0.0,
    )
    d = rec.to_dict()
    assert d["handoff_state"] is None


# ---------------------------------------------------------------------------
# Bridge timing / step counts
# ---------------------------------------------------------------------------


async def test_helm_step_increments_timing_and_count(
    make_bridge, make_visitor, stub_helm
):
    """A single helm.step() call appends to helm_timings_seconds and helm_step_counts."""
    helm = stub_helm(name="A", script=[EMIT(text="ok", finalize=True)])
    bridge = make_bridge(helms={"A": helm}, default_helm="A")
    visitor = make_visitor()

    await bridge.execute(visitor)

    # State was cleared on finalize → check interaction.parameters instead.
    params = _read_params(visitor)
    obs = params.get("bridge_observability")
    assert obs is not None
    assert "A" in obs["helm_timings_seconds"]
    assert obs["helm_timings_seconds"]["A"] >= 0.0
    assert obs["helm_step_counts"]["A"] == 1


async def test_continue_loop_accumulates_step_count(
    make_bridge, make_visitor, stub_helm
):
    """Multiple CONTINUE-then-EMIT visits each increment the same helm's step count."""
    from jvagent.action.helm.contracts import CONTINUE

    helm = stub_helm(
        name="A",
        script=[
            CONTINUE(reason="r1"),
            CONTINUE(reason="r2"),
            EMIT(text="done", finalize=True),
        ],
    )
    bridge = make_bridge(helms={"A": helm}, default_helm="A")
    visitor = make_visitor()

    await bridge.execute(visitor)
    await bridge.execute(visitor)
    await bridge.execute(visitor)

    params = _read_params(visitor)
    obs = params["bridge_observability"]
    assert obs["helm_step_counts"]["A"] == 3


async def test_shift_records_step_counts_per_helm(make_bridge, make_visitor, stub_helm):
    a = stub_helm(name="A", script=[SHIFT(target="B", reason="hand off")])
    b = stub_helm(name="B", script=[EMIT(text="done", finalize=True)])
    bridge = make_bridge(helms={"A": a, "B": b}, default_helm="A")
    visitor = make_visitor()

    await bridge.execute(visitor)  # A.step → SHIFT
    await bridge.execute(visitor)  # B.step → EMIT

    params = _read_params(visitor)
    obs = params["bridge_observability"]
    assert obs["helm_step_counts"]["A"] == 1
    assert obs["helm_step_counts"]["B"] == 1


# ---------------------------------------------------------------------------
# helm_shift event emission
# ---------------------------------------------------------------------------


async def test_initial_helm_resolution_emits_helm_shift_event(
    make_bridge, make_visitor, stub_helm
):
    helm = stub_helm(name="A", script=[EMIT(text="ok", finalize=True)])
    bridge = make_bridge(helms={"A": helm}, default_helm="A")
    visitor = make_visitor()
    visitor.interaction.observability_metrics = []

    await bridge.execute(visitor)

    events = [
        e
        for e in visitor.interaction.observability_metrics
        if e.get("event_type") == "helm_shift"
    ]
    assert len(events) == 1
    initial = events[0]["data"]
    assert initial["from_helm"] is None
    assert initial["to_helm"] == "A"
    assert initial["reason"] == "bridge:initial"
    assert initial["shift_index"] == 0
    assert initial["ack_emitted"] is False


async def test_explicit_shift_emits_separate_helm_shift_event(
    make_bridge, make_visitor, stub_helm
):
    a = stub_helm(name="A", script=[SHIFT(target="B", reason="user wants B")])
    b = stub_helm(name="B", script=[])
    bridge = make_bridge(helms={"A": a, "B": b}, default_helm="A")
    visitor = make_visitor()
    visitor.interaction.observability_metrics = []

    await bridge.execute(visitor)

    events = [
        e
        for e in visitor.interaction.observability_metrics
        if e.get("event_type") == "helm_shift"
    ]
    # One for initial (None→A), one for explicit (A→B).
    assert len(events) == 2
    assert events[0]["data"]["to_helm"] == "A"
    assert events[1]["data"]["from_helm"] == "A"
    assert events[1]["data"]["to_helm"] == "B"
    assert events[1]["data"]["reason"] == "user wants B"


async def test_helm_shift_event_skipped_when_no_interaction_metrics(
    make_bridge, make_visitor, stub_helm
):
    """When the interaction doesn't expose observability_metrics, the event
    emission silently no-ops — turn must NOT fail because of this."""
    helm = stub_helm(name="A", script=[EMIT(text="ok", finalize=True)])
    bridge = make_bridge(helms={"A": helm}, default_helm="A")
    visitor = make_visitor()
    # Strip the metrics list entirely.
    visitor.interaction.observability_metrics = None

    # Should not raise.
    await bridge.execute(visitor)


# ---------------------------------------------------------------------------
# bridge_observability persistence
# ---------------------------------------------------------------------------


async def test_persistence_writes_gear_trace_on_terminal_emit(
    make_bridge, make_visitor, stub_helm
):
    helm = stub_helm(name="A", script=[EMIT(text="done", finalize=True)])
    bridge = make_bridge(helms={"A": helm}, default_helm="A")
    visitor = make_visitor()

    await bridge.execute(visitor)

    obs = _read_params(visitor)["bridge_observability"]
    assert obs["shift_count"] == 1  # the initial None→A shift
    assert len(obs["gear_trace"]) == 1
    assert obs["gear_trace"][0]["to_helm"] == "A"
    assert obs["gear_trace"][0]["from_helm"] is None


async def test_persistence_writes_gear_trace_on_yield(
    make_bridge, make_visitor, stub_helm
):
    from jvagent.action.helm.contracts import YIELD

    helm = stub_helm(name="A", script=[YIELD()])
    bridge = make_bridge(helms={"A": helm}, default_helm="A")
    visitor = make_visitor()

    await bridge.execute(visitor)

    obs = _read_params(visitor)["bridge_observability"]
    assert len(obs["gear_trace"]) == 1


async def test_persistence_persists_per_helm_timings(
    make_bridge, make_visitor, stub_helm
):
    a = stub_helm(name="A", script=[SHIFT(target="B", reason="x")])
    b = stub_helm(name="B", script=[EMIT(text="done", finalize=True)])
    bridge = make_bridge(helms={"A": a, "B": b}, default_helm="A")
    visitor = make_visitor()

    await bridge.execute(visitor)  # A → SHIFT
    await bridge.execute(visitor)  # B → EMIT

    obs = _read_params(visitor)["bridge_observability"]
    assert set(obs["helm_timings_seconds"].keys()) == {"A", "B"}
    assert all(v >= 0.0 for v in obs["helm_timings_seconds"].values())


async def test_persistence_is_best_effort_when_params_missing(
    make_bridge, make_visitor, stub_helm
):
    """A visitor whose interaction has no ``parameters`` attribute must not
    crash the turn — observability simply doesn't persist."""
    helm = stub_helm(name="A", script=[EMIT(text="ok", finalize=True)])
    bridge = make_bridge(helms={"A": helm}, default_helm="A")
    visitor = make_visitor()
    visitor.interaction.parameters = None  # not a dict; should be skipped

    # Must not raise.
    await bridge.execute(visitor)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_params(visitor: Any) -> dict:
    """Pull the params dict off the visitor's interaction.

    The default conftest creates a MagicMock interaction; we coerce
    ``parameters`` to a real dict on first access so the Bridge
    persistence code's ``isinstance(params, dict)`` check passes.
    """
    if not isinstance(visitor.interaction.parameters, dict):
        visitor.interaction.parameters = {}
    return visitor.interaction.parameters
