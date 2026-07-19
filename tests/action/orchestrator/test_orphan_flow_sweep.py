"""Stale flow task sweep when the owner tool is no longer routable."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.orchestrator.continuation import cancel_orphan_flow_tasks


async def test_cancel_orphan_flow_tasks_cancels_unroutable_owner():
    handle = MagicMock()
    handle.task_type = "SKILL"
    handle.owner_action = "MissingIA"
    handle.blocked_on = []
    handle.cancel = AsyncMock()

    store = MagicMock()
    store.list = MagicMock(return_value=[handle])

    visitor = MagicMock()
    visitor.conversation = MagicMock()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "jvagent.action.orchestrator.continuation._store",
            lambda _c: store,
        )
        n = await cancel_orphan_flow_tasks(visitor, routable_tool_names={"SignupIA"})

    assert n == 1
    handle.cancel.assert_awaited_once()
    assert "orphan" in handle.cancel.await_args.kwargs.get("reason", "")


async def test_cancel_orphan_flow_tasks_keeps_routable_owner():
    handle = MagicMock()
    handle.task_type = "SKILL"
    handle.owner_action = "SignupIA"
    handle.blocked_on = []
    handle.cancel = AsyncMock()

    store = MagicMock()
    store.list = MagicMock(return_value=[handle])

    visitor = MagicMock()
    visitor.conversation = MagicMock()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "jvagent.action.orchestrator.continuation._store",
            lambda _c: store,
        )
        n = await cancel_orphan_flow_tasks(visitor, routable_tool_names={"SignupIA"})

    assert n == 0
    handle.cancel.assert_not_awaited()


async def test_cancel_orphan_flow_tasks_keeps_blocked_gated_parent():
    """A task blocked on a prerequisite is parked, not an orphan — e.g. a gated
    non-turn-lock capability skill (payment) waiting on its account prerequisite.
    Its owner is unroutable during the detour, but it must survive to resume."""
    handle = MagicMock()
    handle.task_type = "SKILL"
    handle.owner_action = "payment"  # unroutable during the account detour
    handle.blocked_on = ["task_account_prov"]
    handle.cancel = AsyncMock()

    store = MagicMock()
    store.list = MagicMock(return_value=[handle])

    visitor = MagicMock()
    visitor.conversation = MagicMock()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "jvagent.action.orchestrator.continuation._store",
            lambda _c: store,
        )
        n = await cancel_orphan_flow_tasks(
            visitor, routable_tool_names={"account_provisioning"}
        )

    assert n == 0
    handle.cancel.assert_not_awaited()
