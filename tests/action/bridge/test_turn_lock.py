"""Tests for turn-lock detection (BRIDGE-ROADMAP §F).

Bridge's turn-lock policy is unconditional: when ``find_turn_lock_owner``
returns a lock owner, Bridge auto-DELEGATEs to it regardless of helm or
utterance. There is no helm-level "interrupt the lock" mechanism — that
was vestigial v0.1 surface and was removed in v0.2.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.bridge.state import BRIDGE_STATE_VISITOR_ATTR
from jvagent.action.bridge.turn_lock import TurnLockOwner, find_turn_lock_owner
from jvagent.action.manifest import Manifest

pytestmark = pytest.mark.asyncio


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
    Each dict is converted to a MagicMock with an ``actions`` attribute
    so ``find_turn_lock_owner``'s ``getattr(inter, "actions", ...)`` path
    works the same as a real Interaction node.

    ``agent_actions`` is a list of Action instances exposed via the
    agent's actions manager.
    """
    interaction = MagicMock()
    interaction.id = "int_current"

    fake_interactions = []
    for turn in history_turns:
        m = MagicMock()
        m.id = turn.get("id", f"int_{len(fake_interactions)}")
        m.actions = turn.get("actions") or []
        fake_interactions.append(m)

    conversation = MagicMock()
    conversation.get_interactions = AsyncMock(return_value=fake_interactions)

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
    conversation.get_interactions = AsyncMock(side_effect=RuntimeError("db down"))
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
    fake = MagicMock()
    fake.id = "int_y"
    fake.actions = ["LockedThing"]
    conversation.get_interactions = AsyncMock(return_value=[fake])
    visitor.conversation = conversation
    result = await find_turn_lock_owner(visitor)
    assert result is None


# ---------------------------------------------------------------------------
# Bridge auto-delegate when turn-lock is active
# ---------------------------------------------------------------------------


async def test_execute_auto_delegates_to_lock_owner(
    make_bridge, make_visitor, stub_helm, monkeypatch
):
    """When ``find_turn_lock_owner`` returns a locked action, Bridge runs
    the locked action via DELEGATE without calling helm.step() — regardless
    of the helm. There is no helm-level escape hatch for the lock."""
    helm = stub_helm(name="A", script=[])  # script empty — helm should NOT run
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


async def test_auto_delegate_unconditional_for_any_helm(
    make_bridge, make_visitor, stub_helm, monkeypatch
):
    """Turn-lock auto-DELEGATE fires for ALL helms — there is no escape.

    Earlier designs let helms with ``can_interrupt=True`` bypass the
    lock to emit ``SHIFT(interrupt=True)``. Live testing showed that
    helms don't know about active locks and routinely mis-classified
    fragments mid-flow. The current contract: auto-DELEGATE always
    when a lock is active. The rails IA's own intent classifier
    (e.g. an interview's CANCELLATION intent reading
    ``manifest.interrupt_phrases``) decides whether the user wants
    to break the flow.
    """
    from jvagent.action.helm.contracts import EMIT

    helm = stub_helm(name="A", script=[EMIT(text="reflex emit", finalize=True)])
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

    # Locked action ran; helm did NOT.
    locked_action.execute.assert_awaited_once_with(visitor)
    assert helm.call_count == 0


async def test_auto_delegate_handles_action_raise(
    make_bridge, make_visitor, stub_helm, monkeypatch, publish_log
):
    helm = stub_helm(name="A", script=[])
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
