"""TurnRun recovery states must stop the orchestrator before any new effects."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from jvagent.harness.contracts import NativeCaller, TurnRunState
from jvagent.harness.runtime import HarnessRuntime, HarnessStore, reset_runtime


@pytest.mark.asyncio
async def test_recovery_required_checkpoint_never_reenters_turn_execution(
    make_orchestrator, make_visitor, monkeypatch
):
    visitor = make_visitor()
    visitor.agent_id = "agent-1"
    visitor.session_id = "session-1"
    runtime = HarnessRuntime(HarnessStore())
    reset_runtime(runtime)
    caller = NativeCaller("agent-1", "u", "session-1")
    snapshot = runtime.admit_snapshot(caller)
    correlation_id = runtime.new_correlation()
    runtime.start_turn(correlation_id, caller, snapshot, interaction_id="int_1")
    runtime.mark_recovery(correlation_id, reason="crash_after_dispatch")
    visitor.interaction.observability_metrics = [
        {
            "kind": "harness.turn_run",
            "payload": runtime.export_checkpoint(correlation_id),
        }
    ]
    visitor.report = AsyncMock()
    orchestrator = make_orchestrator()
    execute_turn = AsyncMock()
    monkeypatch.setattr(orchestrator, "_execute_turn", execute_turn)

    await orchestrator.execute(visitor)

    execute_turn.assert_not_awaited()
    visitor.report.assert_awaited_once()
    assert visitor.report.await_args.args[0]["recovery_required"] is True
    reset_runtime()


@pytest.mark.asyncio
async def test_cancelled_orchestrator_turn_persists_cancelled_turn_run(
    make_orchestrator, make_visitor, monkeypatch
):
    visitor = make_visitor()
    visitor.agent_id = "agent-cancel"
    visitor.session_id = "session-cancel"
    visitor.correlation_id = "cancel-run-1"
    runtime = HarnessRuntime(HarnessStore())
    reset_runtime(runtime)
    orchestrator = make_orchestrator()
    monkeypatch.setattr(
        orchestrator,
        "_execute_turn",
        AsyncMock(side_effect=asyncio.CancelledError),
    )

    with pytest.raises(asyncio.CancelledError):
        await orchestrator.execute(visitor)

    run = runtime.get_run("cancel-run-1")
    assert run is not None
    assert run.state is TurnRunState.CANCELLED
    checkpoint = runtime.checkpoint_from_interaction(visitor.interaction)
    assert checkpoint is not None
    assert checkpoint["state"] == TurnRunState.CANCELLED.value
    reset_runtime()
