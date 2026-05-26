"""Tests for turn-lock detection and interrupt gating (BRIDGE-ROADMAP §F)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.bridge.bridge_interact_action import BridgeInteractAction
from jvagent.action.bridge.state import BRIDGE_STATE_VISITOR_ATTR
from jvagent.action.bridge.turn_lock import (
    TurnLockOwner,
    find_turn_lock_owner,
    is_interrupt_allowed,
)
from jvagent.action.helm.contracts import SHIFT
from jvagent.action.helm.stub_helm import StubHelm
from jvagent.action.manifest import Manifest

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# is_interrupt_allowed
# ---------------------------------------------------------------------------


async def test_is_interrupt_allowed_true_when_helm_can_interrupt():
    helm = StubHelm()
    helm.can_interrupt = True
    assert is_interrupt_allowed(helm) is True


async def test_is_interrupt_allowed_false_when_helm_cannot_interrupt():
    helm = StubHelm()
    helm.can_interrupt = False
    assert is_interrupt_allowed(helm) is False


async def test_is_interrupt_allowed_false_on_object_without_attribute():
    obj = MagicMock(spec=[])  # no can_interrupt attribute
    assert is_interrupt_allowed(obj) is False


# ---------------------------------------------------------------------------
# find_turn_lock_owner
# ---------------------------------------------------------------------------


def _make_locked_action(class_name: str) -> MagicMock:
    """Build a fake action whose manifest declares turn_lock=True."""
    action = MagicMock()
    action.__class__ = type(class_name, (), {})
    action.get_manifest = MagicMock(
        return_value=Manifest.from_payload({"turn_lock": True})
    )
    return action


def _make_unlocked_action(class_name: str) -> MagicMock:
    """Build a fake action whose manifest declares turn_lock=False."""
    action = MagicMock()
    action.__class__ = type(class_name, (), {})
    action.get_manifest = MagicMock(
        return_value=Manifest.from_payload({"turn_lock": False})
    )
    return action


def _make_visitor_with_history(
    *,
    history_turns,
    agent_actions,
) -> MagicMock:
    """Build a visitor whose conversation reports the given turn history.

    ``history_turns`` is a list of dicts with optional ``actions`` keys.
    ``agent_actions`` is a list of Action instances exposed via the
    agent's actions manager.
    """
    interaction = MagicMock()
    interaction.id = "int_current"

    conversation = MagicMock()
    conversation.get_interaction_history = AsyncMock(return_value=history_turns)

    actions_mgr = MagicMock()
    actions_mgr.get_all_actions = AsyncMock(return_value=agent_actions)

    agent = MagicMock()
    agent.get_actions_manager = AsyncMock(return_value=actions_mgr)

    visitor = MagicMock()
    visitor.interaction = interaction
    visitor.conversation = conversation
    visitor.agent = agent
    return visitor


async def test_find_turn_lock_owner_returns_none_when_no_conversation():
    visitor = MagicMock()
    visitor.conversation = None
    result = await find_turn_lock_owner(visitor)
    assert result is None


async def test_find_turn_lock_owner_returns_none_when_no_history():
    visitor = _make_visitor_with_history(history_turns=[], agent_actions=[])
    result = await find_turn_lock_owner(visitor)
    assert result is None


async def test_find_turn_lock_owner_returns_none_when_history_has_no_locked_action():
    unlocked = _make_unlocked_action("PlainAction")
    visitor = _make_visitor_with_history(
        history_turns=[{"actions": ["PlainAction"]}],
        agent_actions=[unlocked],
    )
    result = await find_turn_lock_owner(visitor)
    assert result is None


async def test_find_turn_lock_owner_returns_owner_for_locked_action():
    locked = _make_locked_action("InterviewInteractAction")
    visitor = _make_visitor_with_history(
        history_turns=[{"actions": ["InterviewInteractAction"]}],
        agent_actions=[locked],
    )
    result = await find_turn_lock_owner(visitor)
    assert isinstance(result, TurnLockOwner)
    assert result.action_name == "InterviewInteractAction"
    assert result.manifest.turn_lock is True


async def test_find_turn_lock_owner_returns_first_locked_in_lookback():
    locked_a = _make_locked_action("FormA")
    locked_b = _make_locked_action("FormB")
    visitor = _make_visitor_with_history(
        history_turns=[
            {"actions": ["FormA"]},
            {"actions": ["FormB"]},
        ],
        agent_actions=[locked_a, locked_b],
    )
    result = await find_turn_lock_owner(visitor)
    # FormA appears first in history → returned first.
    assert result.action_name == "FormA"


async def test_find_turn_lock_owner_handles_conversation_error_gracefully():
    visitor = MagicMock()
    interaction = MagicMock()
    interaction.id = "int_x"
    visitor.interaction = interaction
    conversation = MagicMock()
    conversation.get_interaction_history = AsyncMock(
        side_effect=RuntimeError("db down")
    )
    visitor.conversation = conversation
    result = await find_turn_lock_owner(visitor)
    assert result is None


async def test_find_turn_lock_owner_handles_missing_agent():
    visitor = MagicMock()
    visitor.agent = None
    visitor._agent = None
    visitor.interact_agent = None
    interaction = MagicMock()
    interaction.id = "int_x"
    visitor.interaction = interaction
    conversation = MagicMock()
    conversation.get_interaction_history = AsyncMock(
        return_value=[{"actions": ["LockedThing"]}]
    )
    visitor.conversation = conversation
    result = await find_turn_lock_owner(visitor)
    assert result is None


# ---------------------------------------------------------------------------
# Bridge SHIFT(interrupt=True) gating
# ---------------------------------------------------------------------------


async def test_shift_interrupt_downgraded_for_incapable_helm(
    make_bridge, make_visitor, stub_helm, caplog
):
    """SHIFT(interrupt=True) from a helm without can_interrupt is still
    honoured as a SHIFT, but the interrupt bit is logged as denied."""
    a = stub_helm(
        name="A",
        script=[
            SHIFT(
                target="B",
                reason="trying interrupt",
            )
        ],
    )
    a.can_interrupt = False
    b = stub_helm(name="B")
    bridge = make_bridge(helms={"A": a, "B": b}, default_helm="A")
    visitor = make_visitor()

    # Manually construct a SHIFT with interrupt=True via the script (the
    # convenience builder used above doesn't expose interrupt).
    a.set_script([SHIFT(target="B", reason="x", interrupt=True)])

    with caplog.at_level("WARNING"):
        await bridge.execute(visitor)

    state = getattr(visitor, BRIDGE_STATE_VISITOR_ATTR)
    assert state.current_helm == "B"
    # The warning records the downgrade so operators can audit.
    assert any(
        "interrupt=True" in rec.message and "denied" in rec.message
        for rec in caplog.records
    )


async def test_shift_interrupt_allowed_for_capable_helm(
    make_bridge, make_visitor, stub_helm
):
    a = stub_helm(name="A", script=[SHIFT(target="B", reason="ok", interrupt=True)])
    a.can_interrupt = True
    b = stub_helm(name="B")
    bridge = make_bridge(helms={"A": a, "B": b}, default_helm="A")
    visitor = make_visitor()

    await bridge.execute(visitor)

    state = getattr(visitor, BRIDGE_STATE_VISITOR_ATTR)
    assert state.current_helm == "B"


# ---------------------------------------------------------------------------
# Bridge auto-delegate when turn-lock is active
# ---------------------------------------------------------------------------


async def test_execute_auto_delegates_to_lock_owner_when_helm_cannot_interrupt(
    make_bridge, make_visitor, stub_helm, monkeypatch
):
    """When ``find_turn_lock_owner`` returns a locked action and the
    current helm has ``can_interrupt=False``, Bridge runs the locked
    action via DELEGATE without calling helm.step()."""
    helm = stub_helm(name="A", script=[])  # script empty — helm should NOT run
    helm.can_interrupt = False
    bridge = make_bridge(helms={"A": helm}, default_helm="A")
    visitor = make_visitor()

    locked_action = MagicMock()
    locked_action.execute = AsyncMock()
    lock_owner = TurnLockOwner(
        action_name="InterviewInteractAction",
        action=locked_action,
        manifest=Manifest.from_payload({"turn_lock": True}),
    )

    async def _fake_find(v, lookback_turns=3):
        return lock_owner

    monkeypatch.setattr(
        "jvagent.action.bridge.bridge_interact_action.find_turn_lock_owner",
        _fake_find,
    )

    await bridge.execute(visitor)

    # The locked action ran directly.
    locked_action.execute.assert_awaited_once_with(visitor)
    # The helm did NOT run.
    assert helm.call_count == 0
    # State cleared after auto-delegate.
    assert BRIDGE_STATE_VISITOR_ATTR not in visitor.__dict__


async def test_execute_skips_auto_delegate_when_helm_can_interrupt(
    make_bridge, make_visitor, stub_helm, monkeypatch
):
    """A helm with ``can_interrupt=True`` (e.g. Reflex) is allowed to
    run despite an active turn-lock — it may issue SHIFT(interrupt=True)
    to break the lock cleanly."""
    from jvagent.action.helm.contracts import EMIT

    helm = stub_helm(name="A", script=[EMIT(text="reflex emit", finalize=True)])
    helm.can_interrupt = True
    bridge = make_bridge(helms={"A": helm}, default_helm="A")
    visitor = make_visitor()

    locked_action = MagicMock()
    locked_action.execute = AsyncMock()
    lock_owner = TurnLockOwner(
        action_name="Locked",
        action=locked_action,
        manifest=Manifest.from_payload({"turn_lock": True}),
    )

    async def _fake_find(v, lookback_turns=3):
        return lock_owner

    monkeypatch.setattr(
        "jvagent.action.bridge.bridge_interact_action.find_turn_lock_owner",
        _fake_find,
    )

    await bridge.execute(visitor)

    # Helm ran, locked action did NOT.
    assert helm.call_count == 1
    locked_action.execute.assert_not_called()


async def test_auto_delegate_handles_action_raise(
    make_bridge, make_visitor, stub_helm, monkeypatch, publish_log
):
    helm = stub_helm(name="A", script=[])
    helm.can_interrupt = False
    bridge = make_bridge(
        helms={"A": helm}, default_helm="A", denied_text="auto-delegate failed"
    )
    visitor = make_visitor()

    locked_action = MagicMock()
    locked_action.execute = AsyncMock(side_effect=RuntimeError("boom"))
    lock_owner = TurnLockOwner(
        action_name="Locked",
        action=locked_action,
        manifest=Manifest.from_payload({"turn_lock": True}),
    )

    async def _fake_find(v, lookback_turns=3):
        return lock_owner

    monkeypatch.setattr(
        "jvagent.action.bridge.bridge_interact_action.find_turn_lock_owner",
        _fake_find,
    )

    await bridge.execute(visitor)

    # Safe-fallback published.
    assert publish_log[-1]["content"] == "auto-delegate failed"


async def test_auto_delegate_skipped_when_lock_owner_matches_current_helm(
    make_bridge, make_visitor, stub_helm, monkeypatch
):
    """If the lock owner IS the current helm by name, don't auto-delegate
    (the helm is already running its own locked flow)."""
    from jvagent.action.helm.contracts import EMIT

    helm = stub_helm(name="Locked", script=[EMIT(text="continuing", finalize=True)])
    helm.can_interrupt = False
    bridge = make_bridge(helms={"Locked": helm}, default_helm="Locked")
    visitor = make_visitor()

    lock_owner = TurnLockOwner(
        action_name="Locked",  # same as helm_name
        action=MagicMock(execute=AsyncMock()),
        manifest=Manifest.from_payload({"turn_lock": True}),
    )

    async def _fake_find(v, lookback_turns=3):
        return lock_owner

    monkeypatch.setattr(
        "jvagent.action.bridge.bridge_interact_action.find_turn_lock_owner",
        _fake_find,
    )

    await bridge.execute(visitor)

    assert helm.call_count == 1
    lock_owner.action.execute.assert_not_called()
