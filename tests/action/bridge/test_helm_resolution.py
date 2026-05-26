"""Direct exercise of ``BridgeInteractAction._lookup_helm`` and edge cases.

These tests bypass the conftest monkey-patching that hides the production
``_lookup_helm`` implementation, so the actual ``get_action``-based lookup is
exercised. Also covers two defensive paths:

- ``get_agent`` raising during SHIFT / DELEGATE.
- ``_safe_fallback`` with a blank ``denied_response_text``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.bridge.bridge_interact_action import (
    BridgeConfigurationError,
    BridgeInteractAction,
)
from jvagent.action.bridge.state import BRIDGE_STATE_VISITOR_ATTR, BridgeState
from jvagent.action.helm.contracts import DELEGATE, EMIT, SHIFT
from jvagent.action.helm.stub_helm import StubHelm

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# _lookup_helm — production code path (no monkey-patch)
# ---------------------------------------------------------------------------


async def test_lookup_helm_returns_none_on_get_action_exception(monkeypatch):
    bridge = BridgeInteractAction()

    async def _boom(self, name):
        raise RuntimeError("registry offline")

    monkeypatch.setattr(BridgeInteractAction, "get_action", _boom)

    result = await bridge._lookup_helm("StubHelm")
    assert result is None


async def test_lookup_helm_returns_none_when_action_not_a_helm(monkeypatch):
    bridge = BridgeInteractAction()
    not_a_helm = MagicMock()  # not an instance of BaseHelm

    async def _get(self, name):
        return not_a_helm

    monkeypatch.setattr(BridgeInteractAction, "get_action", _get)

    result = await bridge._lookup_helm("StubHelm")
    assert result is None


async def test_lookup_helm_returns_none_when_action_missing(monkeypatch):
    bridge = BridgeInteractAction()

    async def _get(self, name):
        return None

    monkeypatch.setattr(BridgeInteractAction, "get_action", _get)

    result = await bridge._lookup_helm("MissingHelm")
    assert result is None


async def test_lookup_helm_returns_helm_when_resolved(monkeypatch):
    bridge = BridgeInteractAction()
    helm = StubHelm()

    async def _get(self, name):
        return helm

    monkeypatch.setattr(BridgeInteractAction, "get_action", _get)

    result = await bridge._lookup_helm("StubHelm")
    assert result is helm


async def test_resolve_helms_map_drops_unresolvable_but_keeps_one(monkeypatch):
    bridge = BridgeInteractAction()
    bridge.helms = ["MissingHelm", "StubHelm"]
    helm = StubHelm()

    async def _lookup(self, name):
        return helm if name == "StubHelm" else None

    monkeypatch.setattr(BridgeInteractAction, "_lookup_helm", _lookup)

    resolved = await bridge._resolve_helms_map()
    assert list(resolved.keys()) == ["StubHelm"]


async def test_resolve_helms_map_raises_when_all_unresolvable(monkeypatch):
    bridge = BridgeInteractAction()
    bridge.helms = ["A", "B"]

    async def _lookup(self, name):
        return None

    monkeypatch.setattr(BridgeInteractAction, "_lookup_helm", _lookup)

    with pytest.raises(BridgeConfigurationError):
        await bridge._resolve_helms_map()


# ---------------------------------------------------------------------------
# Defensive paths
# ---------------------------------------------------------------------------


async def test_safe_fallback_skips_publish_when_text_blank(
    make_bridge, make_visitor, stub_helm, publish_log
):
    a = stub_helm(name="A", script=[SHIFT(target="Nope", reason="bad")])
    bridge = make_bridge(
        helms={"A": a},
        default_helm="A",
        denied_text="",  # blank disables the publish
    )
    visitor = make_visitor()

    await bridge.execute(visitor)

    # No publish happened — fallback path still finalises state.
    assert publish_log == []
    assert BRIDGE_STATE_VISITOR_ATTR not in visitor.__dict__


async def test_get_agent_failure_during_shift_still_runs_ac_safely(
    make_bridge, make_visitor, stub_helm, monkeypatch
):
    """When ``get_agent`` raises, Bridge proceeds with ``agent=None`` (AC no-op)."""
    a = stub_helm(name="A", script=[SHIFT(target="B", reason="hand off")])
    b = stub_helm(name="B")
    bridge = make_bridge(helms={"A": a, "B": b}, default_helm="A")
    visitor = make_visitor()

    async def _boom(self):
        raise RuntimeError("agent unavailable")

    monkeypatch.setattr(BridgeInteractAction, "get_agent", _boom)

    await bridge.execute(visitor)

    state = getattr(visitor, BRIDGE_STATE_VISITOR_ATTR)
    assert state.current_helm == "B"


async def test_get_agent_failure_during_delegate_still_runs_ac_safely(
    make_bridge, make_visitor, stub_helm, monkeypatch
):
    a = stub_helm(name="A", script=[DELEGATE(interact_action="HandoffIA")])
    bridge = make_bridge(helms={"A": a}, default_helm="A")
    visitor = make_visitor()

    executed = {"count": 0}

    class _FakeIA:
        async def execute(self, walker):
            executed["count"] += 1

    bridge._test_action_registry["HandoffIA"] = _FakeIA()

    async def _boom(self):
        raise RuntimeError("agent unavailable")

    monkeypatch.setattr(BridgeInteractAction, "get_agent", _boom)

    await bridge.execute(visitor)

    assert executed["count"] == 1


# ---------------------------------------------------------------------------
# Stale current_helm guard
# ---------------------------------------------------------------------------


async def test_stale_current_helm_routes_to_safe_fallback(
    make_bridge, make_visitor, stub_helm, publish_log
):
    """If ``state.current_helm`` references a helm that vanished between visits,
    Bridge safe-falls back instead of crashing."""
    a = stub_helm(name="A", script=[EMIT(text="hi", finalize=True)])
    bridge = make_bridge(helms={"A": a}, default_helm="A", denied_text="stale")
    visitor = make_visitor()

    # Pre-populate state pointing at a helm Bridge can't resolve.
    setattr(
        visitor,
        BRIDGE_STATE_VISITOR_ATTR,
        BridgeState(current_helm="Ghost", turn_started_at=0.0),
    )

    await bridge.execute(visitor)

    assert publish_log[-1]["content"] == "stale"


# ---------------------------------------------------------------------------
# DELEGATE: target resolution via get_action exception path
# ---------------------------------------------------------------------------


async def test_delegate_get_action_exception_routes_to_safe_fallback(
    make_bridge, make_visitor, stub_helm, publish_log, monkeypatch
):
    a = stub_helm(name="A", script=[DELEGATE(interact_action="BoomIA")])
    bridge = make_bridge(
        helms={"A": a}, default_helm="A", denied_text="delegate-failed"
    )
    visitor = make_visitor()

    # Override get_action AFTER make_bridge has installed its own patch.
    async def _get_action(self, name):
        if name == "A":
            return a
        raise RuntimeError("registry broken")

    monkeypatch.setattr(BridgeInteractAction, "get_action", _get_action)

    await bridge.execute(visitor)

    assert publish_log[-1]["content"] == "delegate-failed"
