"""HC-05 / HC-06: two-worker leases, drain, worker loss."""

from __future__ import annotations

import pytest

from jvagent.harness.contracts import NativeCaller, TurnRunState
from jvagent.harness.runtime import (
    AdmissionRefused,
    HarnessRuntime,
    HarnessStore,
    SessionBusy,
)

pytestmark = pytest.mark.harness_conformance


def test_same_session_contention_and_worker_loss():
    store = HarnessStore()
    w1 = HarnessRuntime(store, worker_id="w1")
    w2 = HarnessRuntime(store, worker_id="w2")
    w1.acquire_session_lease("shared")
    with pytest.raises(SessionBusy):
        w2.acquire_session_lease("shared")
    caller = NativeCaller("ag", "u", "shared")
    snap = w1.admit_snapshot(caller)
    corr = w1.new_correlation()
    w1.start_turn(corr, caller, snap)
    w1.bind_lease("shared", corr)
    w1.append_event(
        session_id="shared",
        kind="final",
        message_id="m-final",
        correlation_id=corr,
        snapshot_id=snap.snapshot_id,
    )
    w1.worker_lost("w1")
    assert w1.get_run(corr).state is TurnRunState.RECOVERY_REQUIRED
    assert [e.message_id for e in w2.replay_from("shared")] == ["m-final"]


def test_drain_stops_admissions_keeps_outbox():
    store = HarnessStore()
    w1 = HarnessRuntime(store, worker_id="w1")
    caller = NativeCaller("ag", "u", "s1")
    snap = w1.admit_snapshot(caller)
    corr = w1.new_correlation()
    w1.append_event(
        session_id="s1",
        kind="chunk",
        message_id="m1",
        correlation_id=corr,
        snapshot_id=snap.snapshot_id,
    )
    w1.drain()
    with pytest.raises(AdmissionRefused):
        w1.admit_snapshot(NativeCaller("ag", "u2", "s2"), force_new=True)
    w2 = HarnessRuntime(store, worker_id="w2")
    assert [e.message_id for e in w2.replay_from("s1")] == ["m1"]
