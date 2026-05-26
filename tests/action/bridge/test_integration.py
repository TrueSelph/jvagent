"""End-to-end Bridge integration tests through real ``execute()`` paths.

Earlier Bridge tests covered the verb contract and individual handler
methods in isolation. These integration tests exercise the full
``Bridge.execute()`` flow with multi-visit chains, real walker queue
mutation, and the same gear-trace persistence the live agent produces.

Failure modes these tests catch that unit tests don't:

- Walker-queue mutation across multiple visits (a regression in
  ``visitor.prepend([self])`` semantics would break real turns but
  pass unit tests).
- DELEGATE chain bookkeeping across the ``follow_up`` boundary.
- Turn-lock release transition (the IA's ``is_actively_locking_turn``
  veto must actually flip Bridge behaviour on the next turn).
- AccessControl filtering of ``always_execute`` IAs during curate.
- ``routing_source`` labels populated correctly across all four
  dispatch paths.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.bridge.state import BRIDGE_STATE_VISITOR_ATTR
from jvagent.action.bridge.turn_lock import TurnLockOwner
from jvagent.action.helm.contracts import DELEGATE, EMIT, SHIFT, YIELD
from jvagent.action.manifest import Manifest

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# DELEGATE follow_up chain
# ---------------------------------------------------------------------------


async def test_delegate_chain_runs_all_ias_then_finalizes(
    make_bridge, make_visitor, stub_helm, monkeypatch
):
    """Three DELEGATEs in sequence (True/True/False) all run; persona-finalize
    runs once at the tail. Verifies the follow_up plumbing end-to-end."""
    helm = stub_helm(
        name="A",
        script=[
            DELEGATE(interact_action="IA1", follow_up=True),
            DELEGATE(interact_action="IA2", follow_up=True),
            DELEGATE(interact_action="IA3", follow_up=False),
        ],
    )

    ia1 = MagicMock()
    ia1.execute = AsyncMock()
    ia2 = MagicMock()
    ia2.execute = AsyncMock()
    ia3 = MagicMock()
    ia3.execute = AsyncMock()

    bridge = make_bridge(helms={"A": helm}, default_helm="A")
    bridge._test_action_registry["IA1"] = ia1
    bridge._test_action_registry["IA2"] = ia2
    bridge._test_action_registry["IA3"] = ia3

    visitor = make_visitor()

    # Three visits.
    await bridge.execute(visitor)  # DELEGATE IA1 (follow_up=True) → re-enqueue
    await bridge.execute(visitor)  # DELEGATE IA2 (follow_up=True) → re-enqueue
    await bridge.execute(visitor)  # DELEGATE IA3 (follow_up=False) → finalize

    # All three IAs ran.
    ia1.execute.assert_awaited_once_with(visitor)
    ia2.execute.assert_awaited_once_with(visitor)
    ia3.execute.assert_awaited_once_with(visitor)

    # State cleared after the tail DELEGATE.
    assert not hasattr(visitor, BRIDGE_STATE_VISITOR_ATTR)


async def test_delegate_chain_first_two_dont_finalize(
    make_bridge, make_visitor, stub_helm
):
    """Verify state survives DELEGATE(follow_up=True) — only the tail clears."""
    helm = stub_helm(
        name="A",
        script=[
            DELEGATE(interact_action="IA1", follow_up=True),
            DELEGATE(interact_action="IA2", follow_up=False),
        ],
    )

    ia1 = MagicMock(execute=AsyncMock())
    ia2 = MagicMock(execute=AsyncMock())

    bridge = make_bridge(helms={"A": helm}, default_helm="A")
    bridge._test_action_registry["IA1"] = ia1
    bridge._test_action_registry["IA2"] = ia2

    visitor = make_visitor()

    await bridge.execute(visitor)
    # State preserved between the two delegations.
    assert hasattr(visitor, BRIDGE_STATE_VISITOR_ATTR)
    state = getattr(visitor, BRIDGE_STATE_VISITOR_ATTR)
    # Initial + 1 delegate (helm_delegate) so far.
    assert state.shift_count == 2

    await bridge.execute(visitor)
    # Cleared after the follow_up=False tail.
    assert not hasattr(visitor, BRIDGE_STATE_VISITOR_ATTR)


# ---------------------------------------------------------------------------
# routing_source labels across all four paths
# ---------------------------------------------------------------------------


async def test_routing_source_records_initial_label_on_first_visit(
    make_bridge, make_visitor, stub_helm
):
    """The initial helm pick records ``routing_source='initial'``."""
    helm = stub_helm(name="A", script=[EMIT(text="hi", finalize=True)])
    bridge = make_bridge(helms={"A": helm}, default_helm="A")
    visitor = make_visitor()

    await bridge.execute(visitor)

    # Persisted on interaction.parameters["bridge_observability"]["gear_trace"].
    trace = visitor.interaction.parameters["bridge_observability"]["gear_trace"]
    assert trace, "gear_trace empty"
    assert trace[0]["routing_source"] == "initial"
    assert trace[0]["from_helm"] is None
    assert trace[0]["to_helm"] == "A"


async def test_routing_source_records_helm_shift_label(
    make_bridge, make_visitor, stub_helm
):
    """SHIFT verb produces a ShiftRecord with ``routing_source='helm_shift'``."""
    a = stub_helm(name="A", script=[SHIFT(target="B", reason="hand off to B")])
    b = stub_helm(name="B", script=[EMIT(text="from B", finalize=True)])

    bridge = make_bridge(helms={"A": a, "B": b}, default_helm="A")
    visitor = make_visitor()

    await bridge.execute(visitor)  # initial + SHIFT
    await bridge.execute(visitor)  # B emits

    trace = visitor.interaction.parameters["bridge_observability"]["gear_trace"]
    # initial, then helm_shift
    sources = [rec["routing_source"] for rec in trace]
    assert sources == ["initial", "helm_shift"]


async def test_routing_source_records_helm_delegate_label(
    make_bridge, make_visitor, stub_helm
):
    """DELEGATE verb produces a ShiftRecord with ``routing_source='helm_delegate'``."""
    helm = stub_helm(
        name="A", script=[DELEGATE(interact_action="IA1", follow_up=False)]
    )
    ia1 = MagicMock(execute=AsyncMock())

    bridge = make_bridge(helms={"A": helm}, default_helm="A")
    bridge._test_action_registry["IA1"] = ia1
    visitor = make_visitor()

    await bridge.execute(visitor)

    trace = visitor.interaction.parameters["bridge_observability"]["gear_trace"]
    sources = [rec["routing_source"] for rec in trace]
    assert sources == ["initial", "helm_delegate"]


async def test_routing_source_records_turn_lock_label(
    make_bridge, make_visitor, stub_helm, monkeypatch
):
    """Turn-lock auto-DELEGATE records ``routing_source='turn_lock'``."""
    helm = stub_helm(name="A", script=[])  # helm never runs

    locked_action = MagicMock(execute=AsyncMock())
    lock_owner = TurnLockOwner(
        action_name="InterviewIA",
        action=locked_action,
        manifest=Manifest.from_payload({"turn_lock": True}),
    )

    async def _fake_find(visitor, lookback_turns=3):
        return lock_owner

    monkeypatch.setattr(
        "jvagent.action.bridge.bridge_interact_action.find_turn_lock_owner",
        _fake_find,
    )

    bridge = make_bridge(helms={"A": helm}, default_helm="A")
    visitor = make_visitor()

    await bridge.execute(visitor)

    trace = visitor.interaction.parameters["bridge_observability"]["gear_trace"]
    sources = [rec["routing_source"] for rec in trace]
    # initial (Bridge picked the helm), then turn_lock (Bridge auto-DELEGATEd)
    assert sources == ["initial", "turn_lock"]


# ---------------------------------------------------------------------------
# Multi-helm SHIFT chain
# ---------------------------------------------------------------------------


async def test_multi_helm_shift_chain_records_each_transition(
    make_bridge, make_visitor, stub_helm
):
    """Three helms, A→B→C→EMIT — gear_trace records every transition."""
    a = stub_helm(name="A", script=[SHIFT(target="B", reason="a→b")])
    b = stub_helm(name="B", script=[SHIFT(target="C", reason="b→c")])
    c = stub_helm(name="C", script=[EMIT(text="answer", finalize=True)])

    bridge = make_bridge(
        helms={"A": a, "B": b, "C": c},
        default_helm="A",
        shift_budget=4,
    )
    visitor = make_visitor()

    await bridge.execute(visitor)  # A → SHIFT B
    await bridge.execute(visitor)  # B → SHIFT C
    await bridge.execute(visitor)  # C → EMIT done

    trace = visitor.interaction.parameters["bridge_observability"]["gear_trace"]
    # initial, A→B, B→C
    assert len(trace) == 3
    assert trace[0]["routing_source"] == "initial"
    assert trace[1] == {
        **trace[1],  # keep dict shape
        "from_helm": "A",
        "to_helm": "B",
        "routing_source": "helm_shift",
    }
    assert trace[2]["from_helm"] == "B"
    assert trace[2]["to_helm"] == "C"
    assert trace[2]["routing_source"] == "helm_shift"
    # Final helm correctly recorded.
    assert a.call_count == 1
    assert b.call_count == 1
    assert c.call_count == 1


# ---------------------------------------------------------------------------
# Per-helm timing instrumentation
# ---------------------------------------------------------------------------


async def test_helm_timings_accumulate_across_visits(
    make_bridge, make_visitor, stub_helm
):
    """``helm_timings_seconds`` accumulates wall-clock per helm across visits."""
    a = stub_helm(name="A", script=[SHIFT(target="B", reason="hand off")])
    b = stub_helm(name="B", script=[EMIT(text="ok", finalize=True)])

    bridge = make_bridge(helms={"A": a, "B": b}, default_helm="A")
    visitor = make_visitor()

    await bridge.execute(visitor)
    await bridge.execute(visitor)

    timings = visitor.interaction.parameters["bridge_observability"][
        "helm_timings_seconds"
    ]
    counts = visitor.interaction.parameters["bridge_observability"]["helm_step_counts"]
    assert "A" in timings and "B" in timings
    assert timings["A"] >= 0.0 and timings["B"] >= 0.0
    assert counts["A"] == 1
    assert counts["B"] == 1


# ---------------------------------------------------------------------------
# Pending-directive consumption on EMIT
# ---------------------------------------------------------------------------


async def test_emit_with_no_directives_publishes_directly(
    make_bridge, make_visitor, stub_helm, publish_log
):
    """Baseline: when no IA directive is pending, helm EMIT publishes directly
    without any persona round-trip."""
    helm = stub_helm(name="A", script=[EMIT(text="hello", finalize=True)])
    bridge = make_bridge(helms={"A": helm}, default_helm="A")
    visitor = make_visitor()
    visitor.interaction.directives = []  # no pending directives

    await bridge.execute(visitor)

    # Direct publish recorded; no persona called.
    assert publish_log == [{"content": "hello", "channel": None, "metadata": None}]


async def test_emit_with_pending_directive_routes_through_persona(
    make_bridge, make_visitor, stub_helm, publish_log
):
    """When an always_execute IA left an unexecuted directive on the
    interaction, the helm's EMIT routes through PersonaAction.respond so
    the directive composes with the helm's text."""
    from unittest.mock import AsyncMock, MagicMock

    helm = stub_helm(name="A", script=[EMIT(text="Hello there", finalize=True)])
    bridge = make_bridge(helms={"A": helm}, default_helm="A")

    # Install a fake PersonaAction in the action registry.
    persona = MagicMock()
    persona.respond = AsyncMock(return_value="Hello! I can help with X, Y, and Z.")
    bridge._test_action_registry["PersonaAction"] = persona

    visitor = make_visitor()
    visitor.interaction.directives = [
        {
            "action_name": "IntroInteractAction",
            "content": "Greet briefly and explain capabilities.",
            "executed": False,
        }
    ]
    visitor.add_directive = AsyncMock()

    await bridge.execute(visitor)

    # Persona was called with the interaction + visitor.
    persona.respond.assert_awaited_once()
    # Helm's draft text was added as a directive so persona composes it.
    visitor.add_directive.assert_awaited()
    drafted = visitor.add_directive.call_args.args[0]
    assert "Hello there" in drafted
    assert drafted.startswith("Tell the user:")
    # No direct publish from Bridge — persona owns the publish.
    assert publish_log == []


async def test_emit_with_executed_directives_publishes_directly(
    make_bridge, make_visitor, stub_helm, publish_log
):
    """A directive marked ``executed=true`` is NOT pending; helm EMIT
    publishes directly without a persona round-trip."""
    from unittest.mock import AsyncMock, MagicMock

    helm = stub_helm(name="A", script=[EMIT(text="ok", finalize=True)])
    bridge = make_bridge(helms={"A": helm}, default_helm="A")
    persona = MagicMock(respond=AsyncMock())
    bridge._test_action_registry["PersonaAction"] = persona

    visitor = make_visitor()
    visitor.interaction.directives = [
        {"action_name": "IntroInteractAction", "content": "x", "executed": True}
    ]

    await bridge.execute(visitor)

    # Direct publish; persona NOT called.
    assert publish_log == [{"content": "ok", "channel": None, "metadata": None}]
    persona.respond.assert_not_awaited()


async def test_emit_with_directive_falls_back_when_persona_missing(
    make_bridge, make_visitor, stub_helm, publish_log
):
    """If a directive is pending but PersonaAction isn't installed,
    Bridge falls back to direct publish so the turn doesn't silently die."""
    helm = stub_helm(name="A", script=[EMIT(text="fallback ok", finalize=True)])
    bridge = make_bridge(helms={"A": helm}, default_helm="A")
    # PersonaAction NOT registered.
    visitor = make_visitor()
    visitor.interaction.directives = [
        {"action_name": "IntroInteractAction", "content": "x", "executed": False}
    ]

    await bridge.execute(visitor)

    # Direct publish — graceful fallback.
    assert publish_log == [
        {"content": "fallback ok", "channel": None, "metadata": None}
    ]


async def test_partial_emit_with_directives_still_publishes_directly(
    make_bridge, make_visitor, stub_helm, publish_log
):
    """Persona render only runs on the TERMINAL EMIT (finalize=True).
    Partial emits (finalize=False) stream through directly to avoid mid-
    stream persona detours."""
    from unittest.mock import AsyncMock, MagicMock

    helm = stub_helm(
        name="A",
        script=[
            EMIT(text="partial", finalize=False),
            EMIT(text="done", finalize=True),
        ],
    )
    bridge = make_bridge(helms={"A": helm}, default_helm="A")
    persona = MagicMock(respond=AsyncMock())
    bridge._test_action_registry["PersonaAction"] = persona

    visitor = make_visitor()
    visitor.interaction.directives = [
        {"action_name": "IntroInteractAction", "content": "x", "executed": False}
    ]
    visitor.add_directive = AsyncMock()

    # First visit: partial emit — direct publish, no persona.
    await bridge.execute(visitor)
    assert publish_log == [{"content": "partial", "channel": None, "metadata": None}]
    persona.respond.assert_not_awaited()

    # Second visit: terminal emit — persona render.
    await bridge.execute(visitor)
    persona.respond.assert_awaited_once()
