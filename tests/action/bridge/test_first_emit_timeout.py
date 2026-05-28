"""First-emit timeout safety net.

When no helm publishes within ``first_emit_timeout_ms`` of turn start, Bridge
emits ``safety_net_ack_text`` once per turn so the user sees activity. The
safety net is transient (does not consume the helm's eventual final emit).
"""

from __future__ import annotations

import pytest

from jvagent.action.bridge.state import BRIDGE_STATE_VISITOR_ATTR
from jvagent.action.helm.contracts import CONTINUE, EMIT

pytestmark = pytest.mark.asyncio


async def _force_timeout(visitor) -> None:
    """Backdate ``turn_started_at`` so the next ``execute`` trips the timeout."""
    state = getattr(visitor, BRIDGE_STATE_VISITOR_ATTR)
    state.turn_started_at -= 10.0  # 10 seconds in the past


async def test_safety_net_fires_when_no_emit_within_window(
    make_bridge, make_visitor, stub_helm, publish_log
):
    """Helm runs CONTINUE (no EMIT) past the deadline → safety-net ack publishes."""
    helm = stub_helm(
        name="A",
        script=[
            # First visit: CONTINUE (no EMIT), state persists.
            CONTINUE(),
            # Second visit: EMIT (after deadline backdate).
            EMIT(text="done", finalize=True),
        ],
    )
    bridge = make_bridge(
        helms={"A": helm},
        default_helm="A",
        first_emit_timeout_ms=10,  # tiny window
        safety_text="working-on-it",
    )
    visitor = make_visitor()

    await bridge.execute(visitor)
    await _force_timeout(visitor)
    await bridge.execute(visitor)

    contents = [entry["content"] for entry in publish_log]
    assert "working-on-it" in contents
    assert "done" in contents
    # Safety net ack precedes the final emit.
    assert contents.index("working-on-it") < contents.index("done")


async def test_safety_net_does_not_fire_after_any_emit(
    make_bridge, make_visitor, stub_helm, publish_log
):
    """Once ``last_emit_at`` is set, the safety net is dormant."""
    helm = stub_helm(
        name="A",
        script=[
            EMIT(text="partial", finalize=False),
            EMIT(text="done", finalize=True),
        ],
    )
    bridge = make_bridge(
        helms={"A": helm},
        default_helm="A",
        first_emit_timeout_ms=10,
        safety_text="should-not-appear",
    )
    visitor = make_visitor()

    await bridge.execute(visitor)
    await _force_timeout(visitor)
    await bridge.execute(visitor)

    contents = [entry["content"] for entry in publish_log]
    assert "should-not-appear" not in contents


async def test_safety_net_fires_at_most_once_per_turn(
    make_bridge, make_visitor, stub_helm, publish_log
):
    """A second helm visit after the safety net publishes must not re-trip it."""
    helm = stub_helm(
        name="A",
        script=[
            CONTINUE(),
            CONTINUE(),
            EMIT(text="done", finalize=True),
        ],
    )
    bridge = make_bridge(
        helms={"A": helm},
        default_helm="A",
        first_emit_timeout_ms=10,
        safety_text="working-on-it",
    )
    visitor = make_visitor()

    await bridge.execute(visitor)  # CONTINUE 1
    await _force_timeout(visitor)
    await bridge.execute(visitor)  # safety net fires here, then CONTINUE 2

    # Reset last_emit_at to simulate "no emit yet" between visits even though
    # the safety net set it. The helm state should still prevent re-firing.
    state = getattr(visitor, BRIDGE_STATE_VISITOR_ATTR)
    state.last_emit_at = None
    state.turn_started_at -= 10.0

    await bridge.execute(visitor)  # EMIT done

    contents = [entry["content"] for entry in publish_log]
    assert contents.count("working-on-it") == 1


async def test_safety_net_picks_from_list_of_variants(
    make_bridge, make_visitor, stub_helm, publish_log
):
    """``safety_net_ack_text`` as a list rotates per fire."""
    helm = stub_helm(
        name="A",
        script=[
            CONTINUE(),
            EMIT(text="done", finalize=True),
        ],
    )
    bridge = make_bridge(
        helms={"A": helm},
        default_helm="A",
        first_emit_timeout_ms=10,
        safety_text=["One moment…", "One sec…", "Hmmm…"],
    )
    visitor = make_visitor()

    await bridge.execute(visitor)
    await _force_timeout(visitor)
    await bridge.execute(visitor)

    contents = [entry["content"] for entry in publish_log]
    safety_picks = [c for c in contents if c != "done"]
    assert len(safety_picks) == 1
    assert safety_picks[0] in {"One moment…", "One sec…", "Hmmm…"}


async def test_safety_net_list_filters_empty_entries(
    make_bridge, make_visitor, stub_helm, publish_log
):
    """Empty / whitespace strings in the list are skipped."""
    helm = stub_helm(
        name="A",
        script=[
            CONTINUE(),
            EMIT(text="done", finalize=True),
        ],
    )
    bridge = make_bridge(
        helms={"A": helm},
        default_helm="A",
        first_emit_timeout_ms=10,
        safety_text=["", "   ", "OnlyValid…"],
    )
    visitor = make_visitor()

    await bridge.execute(visitor)
    await _force_timeout(visitor)
    await bridge.execute(visitor)

    contents = [entry["content"] for entry in publish_log]
    safety_picks = [c for c in contents if c != "done"]
    assert safety_picks == ["OnlyValid…"]


async def test_safety_net_empty_list_disables_publish(
    make_bridge, make_visitor, stub_helm, publish_log
):
    """Empty list disables the safety-net publish entirely."""
    helm = stub_helm(
        name="A",
        script=[
            CONTINUE(),
            EMIT(text="done", finalize=True),
        ],
    )
    bridge = make_bridge(
        helms={"A": helm},
        default_helm="A",
        first_emit_timeout_ms=10,
        safety_text=[],
    )
    visitor = make_visitor()

    await bridge.execute(visitor)
    await _force_timeout(visitor)
    await bridge.execute(visitor)

    contents = [entry["content"] for entry in publish_log]
    assert contents == ["done"]


async def test_safety_net_skipped_when_text_blank(
    make_bridge, make_visitor, stub_helm, publish_log
):
    """``safety_net_ack_text=''`` disables the safety net publish."""
    helm = stub_helm(
        name="A",
        script=[
            CONTINUE(),
            EMIT(text="done", finalize=True),
        ],
    )
    bridge = make_bridge(
        helms={"A": helm},
        default_helm="A",
        first_emit_timeout_ms=10,
        safety_text="",  # disabled
    )
    visitor = make_visitor()

    await bridge.execute(visitor)
    await _force_timeout(visitor)
    await bridge.execute(visitor)

    contents = [entry["content"] for entry in publish_log]
    assert contents == ["done"]
