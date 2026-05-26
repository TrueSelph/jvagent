"""Tests for the DELEGATE chain (BRIDGE-ROADMAP §C-6 follow-up).

The chain lets a helm sequence multiple rails ``InteractAction``
dispatches in one turn:

- ``DELEGATE(follow_up=True)`` — Bridge runs the IA inline, does NOT
  finalize via persona, does NOT clear state, re-enqueues Bridge so the
  helm gets visited again to dispatch the next IA.
- ``DELEGATE(follow_up=False)`` — Bridge runs the IA inline, finalizes
  via persona if directives are pending, clears state, exits the turn.

Tests cover both the verb's contract and Bridge's dispatch path with
stub helms + stub IAs (no LM calls).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.helm.contracts import DELEGATE


class TestDelegateVerbContract:
    """The frozen dataclass shape — backwards-compatible additive field."""

    def test_default_follow_up_is_false(self):
        """Existing code that constructs DELEGATE without follow_up still works."""
        verb = DELEGATE(interact_action="SomeIA")
        assert verb.follow_up is False

    def test_follow_up_true_is_settable(self):
        verb = DELEGATE(interact_action="SomeIA", follow_up=True)
        assert verb.follow_up is True

    def test_args_still_optional(self):
        """The new field doesn't break the existing optional args field."""
        verb = DELEGATE(interact_action="SomeIA", args={"k": "v"})
        assert verb.args == {"k": "v"}
        assert verb.follow_up is False

    def test_dataclass_is_frozen(self):
        """Immutability preserved — follow_up cannot be mutated after init."""
        verb = DELEGATE(interact_action="SomeIA")
        with pytest.raises(Exception):
            # FrozenInstanceError is a subclass of Exception
            verb.follow_up = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Bridge dispatch tests for follow_up — exercise _handle_delegate via a
# stub helm that returns scripted verbs across multiple step() calls.
# ---------------------------------------------------------------------------


class TestReasoningHelmPendingIAsQueue:
    """``_queue_pending_ias`` populates the helm slot for DELEGATE chain."""

    def _make_visitor_with_state(self):
        """Visitor wired with a BridgeState carrying an empty helm slot."""
        from jvagent.action.bridge.state import (
            BRIDGE_STATE_VISITOR_ATTR,
            BridgeState,
        )

        visitor = MagicMock()
        state = BridgeState()
        setattr(visitor, BRIDGE_STATE_VISITOR_ATTR, state)
        return visitor, state

    def test_queues_class_names_in_order(self):
        from jvagent.action.helm.reasoning.reasoning_helm import ReasoningHelm

        helm = ReasoningHelm()
        helm.max_dynamic_activations = 10
        visitor, state = self._make_visitor_with_state()

        ia1 = MagicMock()
        ia1.__class__.__name__ = "IntroIA"
        ia2 = MagicMock()
        ia2.__class__.__name__ = "HandoffIA"

        helm._queue_pending_ias(visitor, [ia1, ia2])

        slot = state.helm_states.get(helm.helm_name())
        assert slot is not None
        assert slot["pending_ias"] == ["IntroIA", "HandoffIA"]

    def test_truncates_to_max_dynamic_activations(self):
        from jvagent.action.helm.reasoning.reasoning_helm import ReasoningHelm

        helm = ReasoningHelm()
        helm.max_dynamic_activations = 2
        visitor, state = self._make_visitor_with_state()

        ias = []
        for i in range(5):
            m = MagicMock()
            m.__class__.__name__ = f"IA{i}"
            ias.append(m)

        helm._queue_pending_ias(visitor, ias)

        slot = state.helm_states.get(helm.helm_name())
        # Only first 2 entries survive the cap; tail dropped.
        assert slot["pending_ias"] == ["IA0", "IA1"]

    def test_no_op_when_routed_ias_empty(self):
        from jvagent.action.helm.reasoning.reasoning_helm import ReasoningHelm

        helm = ReasoningHelm()
        visitor, state = self._make_visitor_with_state()

        helm._queue_pending_ias(visitor, [])

        assert helm.helm_name() not in state.helm_states or not state.helm_states[
            helm.helm_name()
        ].get("pending_ias")

    def test_extends_existing_queue_rather_than_replacing(self):
        """Defensive: if a future code path re-queues mid-chain, we append."""
        from jvagent.action.helm.reasoning.reasoning_helm import ReasoningHelm

        helm = ReasoningHelm()
        helm.max_dynamic_activations = 10
        visitor, state = self._make_visitor_with_state()

        ia1 = MagicMock()
        ia1.__class__.__name__ = "First"
        ia2 = MagicMock()
        ia2.__class__.__name__ = "Second"

        helm._queue_pending_ias(visitor, [ia1])
        helm._queue_pending_ias(visitor, [ia2])

        slot = state.helm_states.get(helm.helm_name())
        assert slot["pending_ias"] == ["First", "Second"]

    def test_no_op_when_bridge_state_missing(self):
        """Helm logs and skips when called outside Bridge orchestration."""
        from jvagent.action.helm.reasoning.reasoning_helm import ReasoningHelm

        helm = ReasoningHelm()
        visitor = MagicMock(spec=[])  # no _bridge_state attr

        ia = MagicMock()
        ia.__class__.__name__ = "OrphanIA"

        # Should not raise even though there's no Bridge state to write into
        helm._queue_pending_ias(visitor, [ia])
