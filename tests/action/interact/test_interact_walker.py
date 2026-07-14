"""InteractWalker.on_interact_action dispatch contract: disabled actions are
skipped, run_in_background actions are deferred to the post-interaction queue,
access-denied actions never execute, and a normal action executes inline."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from jvagent.action.interact.interact_walker import InteractWalker


def _walker():
    w = InteractWalker(
        agent_id="agent_1", utterance="hi", channel="default", user_id="u1"
    )
    w.report = AsyncMock()
    w.enforce_interact_action_access = AsyncMock(return_value=True)
    w.record_action_execution = AsyncMock()
    w.interaction = None  # skip the record-before-execute branch
    return w


def _action(*, enabled=True, background=False):
    a = MagicMock()
    a.enabled = enabled
    a.run_in_background = background
    a.label = "act"
    a.weight = 0
    a.get_class_name.return_value = "ActAction"
    a.execute = AsyncMock()
    return a


async def test_disabled_action_is_skipped():
    w = _walker()
    a = _action(enabled=False)
    await w.on_interact_action(a)
    a.execute.assert_not_awaited()
    assert a not in w.background_actions


async def test_background_action_is_deferred_not_executed():
    w = _walker()
    a = _action(background=True)
    await w.on_interact_action(a)
    a.execute.assert_not_awaited()  # deferred to post-interaction phase
    assert a in w.background_actions


async def test_access_denied_action_does_not_execute():
    w = _walker()
    w.enforce_interact_action_access = AsyncMock(return_value=False)
    a = _action()
    await w.on_interact_action(a)
    a.execute.assert_not_awaited()
    assert a not in w.background_actions


async def test_normal_action_executes_inline():
    w = _walker()
    a = _action()
    await w.on_interact_action(a)
    a.execute.assert_awaited_once_with(w)
    assert a not in w.background_actions


async def test_background_defer_still_enforces_access():
    w = _walker()
    w.enforce_interact_action_access = AsyncMock(return_value=False)
    a = _action(background=True)
    await w.on_interact_action(a)
    a.execute.assert_not_awaited()
    assert a not in w.background_actions  # denied before deferral
