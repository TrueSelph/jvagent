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


# NOTE: ADR-0009 removed ReasoningHelm._queue_pending_ias along with
# the engine router. The ``pending_ias`` slot is now populated by the
# engine ``delegate_to_ia`` tool — see
# ``tests/action/helm/reasoning/test_delegate_to_ia.py`` for the queue
# behaviour. The DELEGATE verb contract (TestDelegateVerbContract above)
# and Bridge's verb dispatch are unchanged.
