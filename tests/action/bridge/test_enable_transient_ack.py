"""Tests for ``BridgeInteractAction.enable_transient_ack``.

Master switch for canned lead-in publishes. When True (default), Bridge
publishes:

- The helm's ``transient_ack`` on SHIFT (when the target helm's manifest
  declares ``latency_class in {"deliberate", "long"}`` and a string is
  provided).
- The ``safety_net_ack_text`` on the first-emit timeout.

When False, **both** publish sites are suppressed. The user sees a brief
silence until the helm produces real output. Useful for voice/SMS
channels where transient acks read as spam, or for agents that want a
single deterministic response surface.

These tests pin the off-mode behaviour at both sites. The default-on
behaviour is also exercised here (sanity check) and indirectly by the
broader protocol tests in ``test_protocol.py``.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.bridge.bridge_interact_action import BridgeInteractAction
from jvagent.action.bridge.state import BridgeState
from jvagent.action.helm.contracts import SHIFT


@contextmanager
def _capture_publishes():
    """Swap ``BridgeInteractAction.publish`` for a capture closure.

    Yields the list of captured kwargs. Restores the original method on
    exit so other tests aren't polluted. Bypasses ``monkeypatch`` because
    that fixture has shown reliability problems under pytest-asyncio
    when re-patching the same method across multiple async tests in
    a class.
    """
    captured: list = []
    original = BridgeInteractAction.publish

    async def _publish(self, **kwargs):
        captured.append(kwargs)

    BridgeInteractAction.publish = _publish
    try:
        yield captured
    finally:
        BridgeInteractAction.publish = original


def _make_target_helm(latency: str = "deliberate") -> MagicMock:
    """Build a mock helm whose ack-eligibility resolves correctly.

    Wave-4 wired ``_is_ack_eligible`` to consult ``get_manifest()``
    first, falling back to the attribute. The MagicMock auto-creates
    ``get_manifest`` as a Mock-returning Mock, which fails the
    eligibility check (its ``.latency_class`` is a Mock, not a string).
    Provide a real Manifest so the manifest path resolves the test's
    intended ``latency`` value.
    """
    from jvagent.action.manifest import Manifest

    helm = MagicMock()
    helm.helm_name = lambda: "ReasoningHelm"
    helm.latency_class = latency
    helm.get_manifest = lambda: Manifest(latency_class=latency)
    return helm


def _make_current_helm() -> MagicMock:
    helm = MagicMock()
    helm.helm_name = lambda: "ReflexHelm"
    return helm


def _make_visitor() -> MagicMock:
    visitor = MagicMock()
    visitor.prepend = AsyncMock()
    return visitor


def _fresh_state() -> BridgeState:
    return BridgeState(
        current_helm="ReflexHelm",
        shift_budget_remaining=4,
        turn_started_at=time.monotonic(),
    )


@pytest.mark.asyncio
class TestEnableTransientAckShiftPath:
    """``_handle_shift``: transient_ack publish gated on the flag."""

    async def test_off_suppresses_shift_transient_ack_publish(self):
        bridge = BridgeInteractAction()
        bridge.enable_transient_ack = False

        verb = SHIFT(
            target="ReasoningHelm",
            reason="needs deliberation",
            transient_ack="Looking that up…",
        )
        target = _make_target_helm()
        state = _fresh_state()

        with _capture_publishes() as captured:
            await bridge._handle_shift(
                _make_visitor(),
                state,
                {"ReasoningHelm": target},
                _make_current_helm(),
                verb,
            )

        # No publish happened.
        assert captured == [], (
            f"enable_transient_ack=False should suppress SHIFT ack publish; "
            f"got {len(captured)} publish call(s)"
        )
        # Bridge still switched helms + recorded the SHIFT (ack_emitted=False).
        assert state.current_helm == "ReasoningHelm"
        assert len(state.shift_log) == 1
        assert state.shift_log[0].ack_emitted is False

    async def test_on_publishes_shift_transient_ack(self):
        bridge = BridgeInteractAction()
        bridge.enable_transient_ack = True

        verb = SHIFT(
            target="ReasoningHelm",
            reason="needs deliberation",
            transient_ack="Looking that up…",
        )
        target = _make_target_helm()
        state = _fresh_state()

        with _capture_publishes() as captured:
            await bridge._handle_shift(
                _make_visitor(),
                state,
                {"ReasoningHelm": target},
                _make_current_helm(),
                verb,
            )

        assert len(captured) == 1
        assert captured[0]["content"] == "Looking that up…"
        assert state.shift_log[0].ack_emitted is True

    async def test_off_skips_publish_even_when_ack_eligible(self):
        """The off-switch dominates the latency-class eligibility check.
        Even an explicitly deliberate helm with a transient_ack string
        gets no publish."""
        bridge = BridgeInteractAction()
        bridge.enable_transient_ack = False

        verb = SHIFT(target="ReasoningHelm", reason="r", transient_ack="X")
        target = _make_target_helm(latency="long")  # also ack-eligible
        state = _fresh_state()

        with _capture_publishes() as captured:
            await bridge._handle_shift(
                _make_visitor(),
                state,
                {"ReasoningHelm": target},
                _make_current_helm(),
                verb,
            )

        assert captured == []


@pytest.mark.asyncio
class TestEnableTransientAckSafetyNet:
    """``_maybe_emit_safety_net``: timeout publish gated on the flag."""

    async def test_off_suppresses_safety_net_publish(self):
        bridge = BridgeInteractAction()
        bridge.enable_transient_ack = False
        bridge.safety_net_ack_text = "Still working on this…"
        bridge.first_emit_timeout_ms = 1  # tiny — deadline immediately passed

        state = _fresh_state()
        # Force the deadline to be in the past, no helm has emitted yet.
        state.turn_started_at = time.monotonic() - 1.0
        state.last_emit_at = None

        with _capture_publishes() as captured:
            await bridge._maybe_emit_safety_net(_make_visitor(), state)

        assert captured == [], (
            f"enable_transient_ack=False should suppress safety-net publish; "
            f"got {captured}"
        )

    async def test_on_publishes_safety_net_when_deadline_passes(self):
        bridge = BridgeInteractAction()
        bridge.enable_transient_ack = True
        bridge.safety_net_ack_text = "Still working on this…"
        bridge.first_emit_timeout_ms = 1

        state = _fresh_state()
        state.turn_started_at = time.monotonic() - 1.0
        state.last_emit_at = None

        with _capture_publishes() as captured:
            await bridge._maybe_emit_safety_net(_make_visitor(), state)

        assert len(captured) == 1
        assert captured[0]["content"] == "Still working on this…"


class TestDefaultValues:
    """Pin the secure-by-default + default-on flags."""

    def test_enable_transient_ack_default_true(self):
        """Pin the current-behaviour default. Flipping this to False is
        a breaking change for operators relying on the lead-in ack."""
        bridge = BridgeInteractAction()
        assert bridge.enable_transient_ack is True

    def test_reasoning_helm_block_raw_tool_invocation_default_true(self):
        """Secure-by-default — protects against tool-name injection
        (the May 2026 adversarial smoke pass demonstrated the gap)."""
        from jvagent.action.helm.reasoning.reasoning_helm import ReasoningHelm

        helm = ReasoningHelm()
        assert helm.block_raw_tool_invocation is True, (
            "block_raw_tool_invocation default must be True. Flipping back "
            "to False reintroduces the tool-name injection surface."
        )

    def test_engine_config_block_raw_tool_invocation_default_true(self):
        from jvagent.action.helm.reasoning.config import EngineConfig

        cfg = EngineConfig(
            model="x",
            router_model="y",
            model_action_type="z",
            router_model_action_type="z",
        )
        assert cfg.block_raw_tool_invocation is True
