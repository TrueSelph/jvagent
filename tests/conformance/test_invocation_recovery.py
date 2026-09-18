"""HC-03 / HC-09: crash and retry leave an explicit recovery result."""

from __future__ import annotations

import pytest

from jvagent.harness.contracts import (
    HarnessContractError,
    IdempotencyClass,
    NativeCaller,
    TurnRunState,
    assert_turn_run_transition,
)
from jvagent.harness.runtime import HarnessRuntime, HarnessStore

pytestmark = pytest.mark.harness_conformance


def test_crash_during_tool_dispatch_is_recovery_required():
    assert_turn_run_transition(TurnRunState.RUNNING, TurnRunState.WAITING_TOOL)
    assert_turn_run_transition(
        TurnRunState.WAITING_TOOL, TurnRunState.RECOVERY_REQUIRED
    )


def test_completed_run_cannot_silently_resume():
    with pytest.raises(HarnessContractError):
        assert_turn_run_transition(TurnRunState.COMPLETED, TurnRunState.RUNNING)


def test_non_retryable_class_exists_for_mutating_tools():
    assert IdempotencyClass.NON_RETRYABLE.value == "non_retryable"


def test_crash_after_dispatch_is_diagnosable_from_journal():
    rt = HarnessRuntime(HarnessStore(), worker_id="w1")
    caller = NativeCaller("ag", "u1", "s1")
    snap = rt.admit_snapshot(caller)
    corr = rt.new_correlation()
    rt.start_turn(corr, caller, snap, interaction_id="int-1")
    rec, _ = rt.begin_invocation(
        correlation_id=corr,
        snapshot_id=snap.snapshot_id,
        tool_name="write",
        payload={"n": 1},
    )
    rt.mark_recovery(corr, reason="crash_after_dispatch")
    journal = rt.get_run(corr)
    assert journal is not None
    assert journal.state is TurnRunState.RECOVERY_REQUIRED
    assert rec.invocation_id
    assert rt.list_journal(corr)


def test_duplicate_dispatch_reuses_invocation_id():
    rt = HarnessRuntime(HarnessStore(), worker_id="w1")
    caller = NativeCaller("ag", "u1", "s1")
    snap = rt.admit_snapshot(caller)
    corr = rt.new_correlation()
    rt.start_turn(corr, caller, snap)
    rec, _ = rt.begin_invocation(
        correlation_id=corr,
        snapshot_id=snap.snapshot_id,
        tool_name="echo",
        payload={"x": 1},
        idempotency_class=IdempotencyClass.IDEMPOTENT,
    )
    rt.finish_invocation(correlation_id=corr, record=rec, result="hello")
    rec2, cached = rt.begin_invocation(
        correlation_id=corr,
        snapshot_id=snap.snapshot_id,
        tool_name="echo",
        payload={"x": 1},
        idempotency_class=IdempotencyClass.IDEMPOTENT,
    )
    assert rec2.invocation_id == rec.invocation_id
    assert cached == "hello"
