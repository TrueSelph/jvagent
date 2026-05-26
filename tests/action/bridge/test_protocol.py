"""Bridge verb-dispatch protocol tests.

One test per :data:`HelmStepResult` verb plus configuration-error paths.
Each test scripts a ``StubHelm`` with the verb under test and asserts:

- the correct publish / prepend / state mutation happens,
- ``BridgeState`` is preserved or cleared per ADR-0007 semantics,
- the helm is called exactly once per Bridge visit (one model call per visit).
"""

from __future__ import annotations

import pytest

from jvagent.action.bridge.bridge_interact_action import (
    BridgeConfigurationError,
    BridgeInteractAction,
)
from jvagent.action.bridge.state import BRIDGE_STATE_VISITOR_ATTR
from jvagent.action.helm.contracts import (
    CONTINUE,
    DELEGATE,
    EMIT,
    EXECUTE,
    SHIFT,
    YIELD,
    ToolCall,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Configuration / refusal
# ---------------------------------------------------------------------------


async def test_execute_refuses_when_no_helms_configured(make_bridge, make_visitor):
    bridge = make_bridge(helms={})  # empty helms map
    visitor = make_visitor()
    with pytest.raises(BridgeConfigurationError):
        await bridge.execute(visitor)


async def test_execute_refuses_when_declared_helm_is_missing(make_bridge, make_visitor):
    bridge = make_bridge(helms={}, helm_names=["MissingHelm"])
    visitor = make_visitor()
    with pytest.raises(BridgeConfigurationError):
        await bridge.execute(visitor)


# ---------------------------------------------------------------------------
# EMIT
# ---------------------------------------------------------------------------


async def test_emit_finalize_publishes_and_clears_state(
    make_bridge, make_visitor, stub_helm, publish_log
):
    helm = stub_helm(name="StubHelm", script=[EMIT(text="hello", finalize=True)])
    bridge = make_bridge(helms={"StubHelm": helm})
    visitor = make_visitor()

    await bridge.execute(visitor)

    # Publish recorded.
    assert publish_log == [{"content": "hello", "channel": None, "metadata": None}]
    # Walker NOT re-enqueued.
    visitor.prepend.assert_not_called()
    # State cleared.
    assert not hasattr(visitor, BRIDGE_STATE_VISITOR_ATTR)
    # Helm called exactly once.
    assert helm.call_count == 1


async def test_emit_non_finalize_publishes_and_reenqueues(
    make_bridge, make_visitor, stub_helm, publish_log
):
    helm = stub_helm(name="StubHelm", script=[EMIT(text="partial", finalize=False)])
    bridge = make_bridge(helms={"StubHelm": helm})
    visitor = make_visitor()

    await bridge.execute(visitor)

    assert publish_log[0]["content"] == "partial"
    visitor.prepend.assert_awaited_once_with([bridge])
    state = getattr(visitor, BRIDGE_STATE_VISITOR_ATTR)
    assert state.finalized is False
    assert state.last_emit_at is not None


# ---------------------------------------------------------------------------
# EXECUTE
# ---------------------------------------------------------------------------


async def test_execute_records_tool_calls_and_reenqueues(
    make_bridge, make_visitor, stub_helm
):
    call = ToolCall(name="memory_set", arguments={"key": "k", "value": "v"})
    helm = stub_helm(name="StubHelm", script=[EXECUTE(tool_calls=[call])])
    bridge = make_bridge(helms={"StubHelm": helm})
    visitor = make_visitor()

    await bridge.execute(visitor)

    visitor.prepend.assert_awaited_once_with([bridge])
    state = getattr(visitor, BRIDGE_STATE_VISITOR_ATTR)
    pending = state.helm_states["StubHelm"]["_pending_tool_calls"]
    assert pending == [
        {"name": "memory_set", "arguments": {"key": "k", "value": "v"}, "call_id": None}
    ]


# ---------------------------------------------------------------------------
# CONTINUE
# ---------------------------------------------------------------------------


async def test_continue_reenqueues_without_state_mutation(
    make_bridge, make_visitor, stub_helm, publish_log
):
    """CONTINUE schedules another visit and does not touch publish / helm slot."""
    helm = stub_helm(name="StubHelm", script=[CONTINUE(reason="awaiting tools")])
    bridge = make_bridge(helms={"StubHelm": helm})
    visitor = make_visitor()

    await bridge.execute(visitor)

    visitor.prepend.assert_awaited_once_with([bridge])
    # No publish.
    assert publish_log == []
    state = getattr(visitor, BRIDGE_STATE_VISITOR_ATTR)
    # Helm slot untouched by Bridge.
    assert "StubHelm" not in state.helm_states
    # Budget unchanged.
    assert state.shift_budget_remaining == bridge.shift_budget_per_turn
    # Shift count is 1 (the initial helm resolution) — no extra shift recorded.
    assert state.shift_count == 1


async def test_continue_without_reason_still_reenqueues(
    make_bridge, make_visitor, stub_helm
):
    helm = stub_helm(name="StubHelm", script=[CONTINUE()])
    bridge = make_bridge(helms={"StubHelm": helm})
    visitor = make_visitor()

    await bridge.execute(visitor)

    visitor.prepend.assert_awaited_once_with([bridge])


async def test_continue_loop_terminates_on_eventual_emit(
    make_bridge, make_visitor, stub_helm, publish_log
):
    """Multiple CONTINUE visits then a final EMIT — natural revisit pattern."""
    helm = stub_helm(
        name="StubHelm",
        script=[
            CONTINUE(reason="round 1"),
            CONTINUE(reason="round 2"),
            EMIT(text="done", finalize=True),
        ],
    )
    bridge = make_bridge(helms={"StubHelm": helm})
    visitor = make_visitor()

    await bridge.execute(visitor)
    await bridge.execute(visitor)
    await bridge.execute(visitor)

    assert helm.call_count == 3
    assert publish_log == [{"content": "done", "channel": None, "metadata": None}]
    assert not hasattr(visitor, BRIDGE_STATE_VISITOR_ATTR)


# ---------------------------------------------------------------------------
# SHIFT
# ---------------------------------------------------------------------------


async def test_shift_to_known_helm_records_trace_and_reenqueues(
    make_bridge, make_visitor, stub_helm
):
    a = stub_helm(name="A", script=[SHIFT(target="B", reason="hand off")])
    b = stub_helm(name="B", script=[])  # B not called this visit
    bridge = make_bridge(helms={"A": a, "B": b}, default_helm="A")
    visitor = make_visitor()

    await bridge.execute(visitor)

    state = getattr(visitor, BRIDGE_STATE_VISITOR_ATTR)
    assert state.current_helm == "B"
    # Two shifts: initial (None→A) and explicit (A→B).
    assert [(r.from_helm, r.to_helm) for r in state.gear_trace] == [
        (None, "A"),
        ("A", "B"),
    ]
    assert state.shift_count == 2
    assert state.shift_budget_remaining == 3  # decremented once
    visitor.prepend.assert_awaited_once_with([bridge])


async def test_shift_to_unknown_helm_triggers_safe_fallback(
    make_bridge, make_visitor, stub_helm, publish_log
):
    a = stub_helm(name="A", script=[SHIFT(target="Nope", reason="bad target")])
    bridge = make_bridge(
        helms={"A": a},
        default_helm="A",
        denied_text="fallback-text",
    )
    visitor = make_visitor()

    await bridge.execute(visitor)

    assert publish_log[-1]["content"] == "fallback-text"
    # State cleared on safe-fallback.
    assert not hasattr(visitor, BRIDGE_STATE_VISITOR_ATTR)


async def test_shift_emits_transient_ack_on_deliberate_target(
    make_bridge, make_visitor, stub_helm, publish_log
):
    a = stub_helm(
        name="A",
        script=[SHIFT(target="B", reason="long task", transient_ack="thinking…")],
    )
    b = stub_helm(name="B", latency_class="deliberate")
    bridge = make_bridge(helms={"A": a, "B": b}, default_helm="A")
    visitor = make_visitor()

    await bridge.execute(visitor)

    contents = [entry["content"] for entry in publish_log]
    assert "thinking…" in contents
    state = getattr(visitor, BRIDGE_STATE_VISITOR_ATTR)
    # The recorded SHIFT (A→B) has ack_emitted=True.
    a_to_b = next(r for r in state.gear_trace if r.from_helm == "A")
    assert a_to_b.ack_emitted is True


async def test_shift_does_not_emit_ack_on_quick_target(
    make_bridge, make_visitor, stub_helm, publish_log
):
    a = stub_helm(
        name="A",
        script=[SHIFT(target="B", reason="quick task", transient_ack="thinking…")],
    )
    b = stub_helm(name="B", latency_class="quick")
    bridge = make_bridge(helms={"A": a, "B": b}, default_helm="A")
    visitor = make_visitor()

    await bridge.execute(visitor)

    assert "thinking…" not in [entry["content"] for entry in publish_log]


async def test_shift_persists_handoff_state_on_target(
    make_bridge, make_visitor, stub_helm
):
    a = stub_helm(
        name="A",
        script=[
            SHIFT(target="B", reason="hand off", handoff_state={"topic": "weather"})
        ],
    )
    b = stub_helm(name="B")
    bridge = make_bridge(helms={"A": a, "B": b}, default_helm="A")
    visitor = make_visitor()

    await bridge.execute(visitor)

    state = getattr(visitor, BRIDGE_STATE_VISITOR_ATTR)
    assert state.helm_states["B"] == {"topic": "weather"}


# ---------------------------------------------------------------------------
# DELEGATE
# ---------------------------------------------------------------------------


async def test_delegate_runs_target_and_reenqueues(
    make_bridge, make_visitor, stub_helm
):
    """DELEGATE resolves the named IA, executes it inline, then re-enqueues Bridge."""
    a = stub_helm(name="A", script=[DELEGATE(interact_action="HandoffIA")])
    bridge = make_bridge(helms={"A": a}, default_helm="A")
    visitor = make_visitor()

    # Register a fake IA in the action registry.
    executed = {"count": 0}

    class _FakeIA:
        async def execute(self, walker):
            executed["count"] += 1

    bridge._test_action_registry["HandoffIA"] = _FakeIA()

    await bridge.execute(visitor)

    assert executed["count"] == 1
    visitor.prepend.assert_awaited_once_with([bridge])
    state = getattr(visitor, BRIDGE_STATE_VISITOR_ATTR)
    assert state.delegated_action is None  # cleared after execute


async def test_delegate_missing_target_triggers_safe_fallback(
    make_bridge, make_visitor, stub_helm, publish_log
):
    a = stub_helm(name="A", script=[DELEGATE(interact_action="Nope")])
    bridge = make_bridge(helms={"A": a}, default_helm="A", denied_text="fallback-text")
    visitor = make_visitor()

    await bridge.execute(visitor)

    assert publish_log[-1]["content"] == "fallback-text"
    assert not hasattr(visitor, BRIDGE_STATE_VISITOR_ATTR)


async def test_delegate_target_raising_triggers_safe_fallback(
    make_bridge, make_visitor, stub_helm, publish_log
):
    a = stub_helm(name="A", script=[DELEGATE(interact_action="Broken")])
    bridge = make_bridge(helms={"A": a}, default_helm="A", denied_text="fallback-text")
    visitor = make_visitor()

    class _BrokenIA:
        async def execute(self, walker):
            raise RuntimeError("boom")

    bridge._test_action_registry["Broken"] = _BrokenIA()

    await bridge.execute(visitor)

    assert publish_log[-1]["content"] == "fallback-text"


# ---------------------------------------------------------------------------
# YIELD
# ---------------------------------------------------------------------------


async def test_yield_clears_state_and_does_not_reenqueue(
    make_bridge, make_visitor, stub_helm
):
    a = stub_helm(name="A", script=[YIELD()])
    bridge = make_bridge(helms={"A": a}, default_helm="A")
    visitor = make_visitor()

    await bridge.execute(visitor)

    visitor.prepend.assert_not_called()
    assert not hasattr(visitor, BRIDGE_STATE_VISITOR_ATTR)


# ---------------------------------------------------------------------------
# Defensive: unknown verb type
# ---------------------------------------------------------------------------


async def test_unknown_verb_triggers_safe_fallback(
    make_bridge, make_visitor, stub_helm, publish_log
):
    """A helm returning a non-verb object must not break Bridge."""
    a = stub_helm(name="A", script=["not-a-verb"])  # str is not a verb
    bridge = make_bridge(helms={"A": a}, default_helm="A", denied_text="fallback")
    visitor = make_visitor()

    await bridge.execute(visitor)

    assert publish_log[-1]["content"] == "fallback"
