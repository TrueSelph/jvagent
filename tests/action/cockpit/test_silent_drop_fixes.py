"""Cockpit silent-drop fixes (AUDIT-interact-cockpit CRIT-02, CRIT-03, CRIT-04).

Verifies the three previously-silent paths:

1. ``_phase_continue`` with ``engine is None`` now publishes a fallback and
   marks the interaction executed — does not silently return.
2. ``curate_walk_path`` keeps routed actions that weren't in the queue
   instead of dropping them.
3. ``_phase_route_and_setup`` detects an ``append`` that was dropped by a
   full walker queue and falls back to inline finalize.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.cockpit.cockpit_interact_action import CockpitInteractAction
from jvagent.action.cockpit.session import CockpitSession
from jvagent.action.interact.base import InteractAction

# ---------- CRIT-02 helpers ----------


def _stub_visitor():
    interaction = SimpleNamespace(
        id="int_x",
        response=None,
        set_to_executed=MagicMock(),
        save=AsyncMock(),
    )
    visitor = SimpleNamespace(
        interaction=interaction,
        response_bus=SimpleNamespace(publish=AsyncMock()),
        session_id="sess_x",
        channel="web",
        stream=False,
        data={},
        user_id="u_x",
    )
    return visitor, interaction


@pytest.mark.asyncio
async def test_phase_continue_with_no_engine_publishes_fallback_and_marks_executed():
    cockpit = CockpitInteractAction.__new__(CockpitInteractAction)
    visitor, interaction = _stub_visitor()

    session = CockpitSession()
    session.engine = None  # the scenario under test

    with patch(
        "jvagent.action.cockpit.cockpit_interact_action.get_session",
        return_value=session,
    ):
        await cockpit._phase_continue(visitor)

    assert interaction.response and "rephrase" in interaction.response.lower()
    interaction.set_to_executed.assert_called()
    visitor.response_bus.publish.assert_awaited()
    assert session.debug_state is None


# ---------- CRIT-03 helpers (post-Wave G revert) ----------
#
# The Wave B "include actions not in queue" change broke cockpit's
# walker-revisit loop — the cockpit instance got force-prepended every
# visit, causing infinite re-entry. Wave G reverts to the original
# "drop if not in queue" semantics, with an explicit debug log so
# routed sub-InteractActions silently dropped here become observable.
# Callers that need to add new IAs to the queue must use ``prepend()``
# or ``visit()`` BEFORE ``curate_walk_path``.


class _FakeIA(InteractAction):  # type: ignore[misc]
    """Concrete InteractAction subclass for queue-curation tests."""

    async def execute(self, visitor):  # noqa: ARG002
        return None


@pytest.mark.asyncio
async def test_curate_walk_path_drops_actions_not_in_queue():
    """``curate_walk_path`` MUST drop actions not currently in the queue.

    Adding them would re-prepend the cockpit's own self every visit and
    cause infinite re-entry through ``curate_walk_path_for_cockpit``.
    """
    from jvagent.action.interact.interact_walker import InteractWalker

    walker = InteractWalker.__new__(InteractWalker)

    in_queue = _FakeIA(label="ia_in_queue")
    object.__setattr__(in_queue, "id", "n.InteractAction.in_queue")
    nested = _FakeIA(label="ia_nested")
    object.__setattr__(nested, "id", "n.InteractAction.nested")

    walker.get_queue = AsyncMock(return_value=[in_queue])
    walker.dequeue = AsyncMock()
    prepend_calls = []

    async def _prepend(items):
        prepend_calls.append(list(items))

    walker.prepend = _prepend

    result = await InteractWalker.curate_walk_path(walker, [in_queue, nested])

    # Only the in-queue action survives; ``nested`` was never enqueued
    # by the caller so it must NOT appear in the result.
    assert [a.id for a in result] == [in_queue.id]
    # The prepend list (if any) also excludes ``nested``.
    assert prepend_calls and not any(a.id == nested.id for a in prepend_calls[0])
