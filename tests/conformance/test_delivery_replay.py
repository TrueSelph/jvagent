"""HC-04: ordered cursor replay, single final response."""

from __future__ import annotations

import pytest

from jvagent.harness.contracts import EventEnvelope, HarnessContractError, NativeCaller
from jvagent.harness.runtime import HarnessRuntime, HarnessStore

pytestmark = pytest.mark.harness_conformance


def test_event_envelopes_order_by_sequence():
    a = EventEnvelope(
        session_id="s1",
        sequence=1,
        cursor="s1:1",
        message_id="m1",
        correlation_id="c1",
        snapshot_id="snap-1",
        kind="chunk",
    )
    b = EventEnvelope(
        session_id="s1",
        sequence=2,
        cursor="s1:2",
        message_id="m2",
        correlation_id="c1",
        snapshot_id="snap-1",
        kind="final",
    )
    assert a.sequence < b.sequence
    assert a.cursor != b.cursor


def test_event_envelope_rejects_non_positive_sequence():
    with pytest.raises(HarnessContractError):
        EventEnvelope(
            session_id="s1",
            sequence=0,
            cursor="s1:0",
            message_id="m0",
            correlation_id="c1",
            snapshot_id="snap-1",
            kind="chunk",
        )


def test_reconnecting_client_replays_missed_frames_in_order():
    store = HarnessStore()
    w1 = HarnessRuntime(store, worker_id="w1")
    w2 = HarnessRuntime(store, worker_id="w2")
    caller = NativeCaller("ag", "u1", "s1")
    snap = w1.admit_snapshot(caller)
    corr = w1.new_correlation()
    w1.append_event(
        session_id="s1",
        kind="chunk",
        message_id="m1",
        correlation_id=corr,
        snapshot_id=snap.snapshot_id,
    )
    w1.append_event(
        session_id="s1",
        kind="final",
        message_id="m2",
        correlation_id=corr,
        snapshot_id=snap.snapshot_id,
    )
    replayed = w2.replay_from("s1", "s1:1")
    assert [e.message_id for e in replayed] == ["m2"]
    assert [e.sequence for e in replayed] == [2]
